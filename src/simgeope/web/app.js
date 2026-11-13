// SimGeoPE viewer bootstrap: map, basemap theme, mode switching, layer visibility.
// No build step: native ES modules served from /static.
import {$, get, status} from './common.js';
import {WORLD_GROUPS, addWorldLayers, paintWorld, renderMetadata, createWorld} from './world.js';
import {SIM_GROUPS, addSimLayers, createSim} from './sim.js';

// Keyless OSM tiles, desaturated so status colours stand out; the dark theme
// inverts their brightness range (min above max) instead of using another tile set.
const OSM_TILES = ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'];
const OSM_ATTRIBUTION = '© OpenStreetMap contributors';
const BASEMAP_PAINT = {
  light: {'raster-saturation': -0.85, 'raster-contrast': -0.15, 'raster-brightness-min': 0.12, 'raster-brightness-max': 1},
  dark: {'raster-saturation': -1, 'raster-contrast': -0.2, 'raster-brightness-min': 0.78, 'raster-brightness-max': 0.06},
};
const MOVE_DEBOUNCE_MS = 200;
const darkScheme = matchMedia('(prefers-color-scheme: dark)');

let map, world, sim, mode = 'world';

const checkedLayers = (view) => new Set([...document.querySelectorAll(`[data-view="${view}"] [data-layer]:checked`)].map(input => input.dataset.layer));

function visibility() {
  const groups = mode === 'sim' ? SIM_GROUPS : WORLD_GROUPS;
  const visible = new Set([...checkedLayers(mode)].flatMap(key => groups[key]));
  if (mode === 'world') WORLD_GROUPS.route.forEach(id => visible.add(id));
  const hidden = mode === 'sim' ? sim.hiddenLayers() : new Set();
  const managed = new Set([...Object.values(WORLD_GROUPS), ...Object.values(sim ? SIM_GROUPS : {})].flat());
  for (const id of managed) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible.has(id) && !hidden.has(id) ? 'visible' : 'none');
  }
}

function setMode(next) {
  mode = next;
  document.body.dataset.current = mode;
  for (const element of document.querySelectorAll('[data-view]')) {
    // The farm card opens on demand; leaving its view only hides it.
    element.hidden = element.dataset.view !== mode || (element.id === 'farm-card' && !element.dataset.open);
  }
  for (const tab of document.querySelectorAll('[role=tab]')) tab.setAttribute('aria-selected', String(tab.dataset.targetMode === mode));
  visibility();
  if (mode === 'world') { sim?.pause(); world.refresh(); }
  else { world.cancel(); status('Simulação pronta.'); }
}

function applyTheme() {
  const dark = darkScheme.matches;
  for (const [property, value] of Object.entries(BASEMAP_PAINT[dark ? 'dark' : 'light'])) map.setPaintProperty('basemap', property, value);
  paintWorld(map);
  sim?.paint();
}

async function start() {
  if (!window.maplibregl) { status('O MapLibre não carregou: o navegador precisa de internet para o CDN.', 'error'); return; }
  const metadata = await get('/api/world/metadata');
  renderMetadata(metadata);
  try {
    map = new maplibregl.Map({
      container: 'map', center: [-37, -8], zoom: 6, renderWorldCopies: false,
      style: {
        version: 8,
        sources: {osm: {type: 'raster', tiles: OSM_TILES, tileSize: 256, maxzoom: 19, attribution: OSM_ATTRIBUTION}},
        // Theme changes repaint this layer (setStyle would drop every data source and layer).
        layers: [{id: 'basemap', type: 'raster', source: 'osm', paint: BASEMAP_PAINT.light}],
      },
    });
  } catch (error) {
    // MapLibre reports a failed WebGL context as a JSON message with requestedAttributes.
    if (!error.message.includes('"requestedAttributes"')) throw error;
    console.error(error);
    status('O mapa não pôde ser criado: o navegador não ofereceu um contexto WebGL.', 'error');
    return;
  }
  // Style/expression errors would otherwise reach only the console; tile
  // download failures (offline basemap) are expected and stay there.
  map.on('error', event => { if (!event.tile && !event.sourceId) status(`Mapa: ${event.error?.message ?? 'erro desconhecido'}`, 'error'); });
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
  map.fitBounds([[metadata.bounds[0], metadata.bounds[1]], [metadata.bounds[2], metadata.bounds[3]]], {padding: 30, duration: 0});
  new ResizeObserver(() => map.resize()).observe($('map'));

  map.on('load', async () => {
    addWorldLayers(map);
    world = createWorld(map, {enabledLayers: () => checkedLayers('world')});
    if (metadata.simulation) {
      addSimLayers(map);
      sim = createSim(map, {onLayersChanged: visibility, isActive: () => mode === 'sim'});
    }
    applyTheme();
    darkScheme.addEventListener('change', applyTheme);

    let moveTimer;
    map.on('moveend', () => {
      if (mode !== 'world') return;
      clearTimeout(moveTimer);
      moveTimer = setTimeout(world.refresh, MOVE_DEBOUNCE_MS);
    });
    document.querySelectorAll('[data-layer]').forEach(input => input.addEventListener('change', () => {
      visibility();
      if (mode === 'world') { sim?.pause(); world.refresh(); }
    }));
    map.on('click', event => {
      if (mode === 'sim') { sim.handleClick(event); return; }
      const layers = Object.values(WORLD_GROUPS).flat().filter(id => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
      world.handleClick(event, map.queryRenderedFeatures(event.point, {layers}));
    });

    try { map.getSource('admin').setData(await get('/api/world/municipalities')); }
    catch (error) { status(`Municípios: ${error.message}`, 'error'); }

    if (metadata.simulation) {
      $('mode-tabs').hidden = false;
      for (const tab of document.querySelectorAll('[role=tab]')) {
        tab.addEventListener('click', () => setMode(tab.dataset.targetMode));
        tab.addEventListener('keydown', event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          const other = document.querySelector(`[role=tab]:not([data-target-mode="${tab.dataset.targetMode}"])`);
          other.focus();
          setMode(other.dataset.targetMode);
        });
      }
      setMode('sim');
      await sim.start();
    } else {
      // Without --simulate the page is the world inspector only.
      setMode('world');
    }
  });
}

start().catch(error => status(error.message, 'error'));
