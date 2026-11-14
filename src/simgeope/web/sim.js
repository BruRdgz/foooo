// Live H5N1 simulation: playback, day replay, KPIs, charts, farm card, event
// timeline and the scenario (parameter) editor.
import {
  $, h, empty, sleep, get, post, isAbort, ApiError, status, token, fmt,
  STATUSES, statusLabel, PATHWAYS, SEIR, kindLabel, eventLabel, eventCategory, EVENT_CATEGORIES,
  ZONE_RINGS, zoneLabel, PROTOCOL_SOURCES, PRODUCTIONS, PRODUCTS,
} from './common.js';
import {timeChart, segmentedBar} from './charts.js';

export const SIM_GROUPS = {
  simFarms: ['sim-farms', 'sim-farms-type', 'sim-focus', 'sim-selected'],
  simHubs: ['sim-hubs'],
  simTrucks: ['sim-trucks-intro', 'sim-trucks'],
  simBirds: ['sim-birds'],
  simRisk: ['sim-risk'],
  simInterventions: ['sim-interventions-fill', 'sim-interventions-line'],
  simAdmin: ['admin-line'],
  simChain: ['sim-chain-casing', 'sim-chain', 'sim-chain-local', 'sim-chain-arrows', 'sim-chain-seed', 'sim-chain-hubs'],
  simFlocks: ['sim-flocks-halo', 'sim-flocks', 'sim-flocks-site'],
  simHeat: ['sim-heat'],
  simZones: ['sim-zones-fill', 'sim-zones-standstill', 'sim-zones-casing', ...ZONE_RINGS.map(r => `sim-zones-${r.key}`)],
};
const REPLAY_SOURCES = ['farms', 'hubs', 'birds', 'movements', 'interventions'];
const LIVE_SOURCES = [...REPLAY_SOURCES, 'risk'];
// Fetched on their own schedule, only while their layer is checked.
const OVERLAY_SOURCES = ['heat', 'chain', 'chain-hubs', 'flocks', 'zones', 'zones-edge'];
const CHAIN_CLICK_LAYERS = ['sim-chain', 'sim-chain-local', 'sim-chain-seed'];
const FLOCK_CLICK_LAYERS = ['sim-flocks', 'sim-flocks-site'];
const PAUSE_MS = {slow: 900, normal: 350, fast: 60};  // pause between steps while running
const SCRUB_DEBOUNCE_MS = 120;
const MAX_EVENTS_KEPT = 2000;   // in-memory event log cap
const TIMELINE_ROWS = 300;      // rows rendered at once
const CARD_ITEMS = 8;           // recent events / truck visits in the farm card
const TABLE_DAYS = 30;          // days in the "Ver dados" table
const RT_AVERAGE_DAYS = 7;      // trailing window of the smoothed Rt line
const HEAT_RECENT_DAYS = 30;    // "últimos 30 dias" heat window
const FLOCK_DAYS = 7;           // flock arrivals shown up to the day on screen
const HUB_BARS = 10;            // bars in "Bases na cadeia" (real worlds have ~4–10 hubs)
const DAY_MS = 86_400_000;
const MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
// Simulation dates are calendar days: format them in UTC so no time zone shifts the day.
const DATE_FORMAT = new Intl.DateTimeFormat('pt-BR', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'});
const SHORT_DATE_FORMAT = new Intl.DateTimeFormat('pt-BR', {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'});
const CLICK_SLOP_PX = 5;        // thin chain lines are hard to hit exactly

// ---------------------------------------------------------------- map layers

/** Signed-distance-field icon from `distance(x, y)` in pixels (negative inside). */
function sdfIcon(size, distance) {
  const data = new Uint8ClampedArray(size * size * 4);
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      // MapLibre SDF icons: alpha 0.75 at the edge, falling 1/8 per pixel outside.
      const offset = (row * size + col) * 4;
      data.set([255, 255, 255, Math.round(255 * Math.max(0, Math.min(1, .75 - distance(col + .5, row + .5) / 8)))], offset);
    }
  }
  return {width: size, height: size, data};
}

/** Diamond, so hubs read as a different shape from farms. */
const diamondIcon = () => sdfIcon(40, (x, y) => (Math.abs(x - 20) + Math.abs(y - 20) - 13) / Math.SQRT2);

/** Hollow square: focus farms get a frame no other farm or hub mark uses. */
const squareIcon = () => sdfIcon(40, (x, y) => Math.abs(Math.max(Math.abs(x - 20), Math.abs(y - 20)) - 11) - 1.25);

/** Arrowhead pointing +x: line-placed symbols turn it along the line's direction. */
function arrowIcon() {
  const corners = [[24, 14], [9, 6], [9, 22]];  // tip, then the two back corners (28 px icon)
  const edges = corners.map((a, i) => {
    const b = corners[(i + 1) % corners.length];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    return {a, nx: (a[1] - b[1]) / length, ny: (b[0] - a[0]) / length};  // outward for this corner order
  });
  // Convex polygon: the largest signed distance to an edge line.
  return sdfIcon(28, (x, y) => Math.max(...edges.map(({a, nx, ny}) => (x - a[0]) * nx + (y - a[1]) * ny)));
}

const byStatus = (values, fallback) => ['match', ['get', 'status'], ...Object.entries(values).flat(), fallback];
const IS_LINE = ['==', ['geometry-type'], 'LineString'];
// Chain width follows the share of the hazard attributed to the source (1 for fomite and birds).
const CHAIN_WIDTH = ['interpolate', ['linear'], ['get', 'weight'], 0, 1.25, 1, 3.5];
const CHAIN_CASING_WIDTH = ['interpolate', ['linear'], ['get', 'weight'], 0, 3.75, 1, 6];
// Ring area grows with the flock's birds (36 birds → 16 px); older arrivals fade.
const FLOCK_RADIUS = ['interpolate', ['linear'], ['sqrt', ['get', 'size']], 1, 5, 6, 16];
const FLOCK_FADE = ['interpolate', ['linear'], ['get', 'age'], 0, 1, FLOCK_DAYS - 1, .35];
// Emergency rings: quiet fills (lighter outward) and one line layer per ring,
// since dash patterns cannot be data-driven. Outer rings are more broken.
const ZONE_FILL_OPACITY = ['match', ['get', 'ring'], 'perifoco', .1, 'vigilancia', .05, .025];
const ZONE_LINES = {
  perifoco: {'line-width': 2},
  vigilancia: {'line-width': 1.5, 'line-dasharray': [4, 2.5]},
  protecao: {'line-width': 1.5, 'line-dasharray': [1.2, 2]},
};
const IS_RING = (key) => ['==', ['get', 'ring'], key];
const HAS_SITE = ['==', ['typeof', ['get', 'site']], 'string'];
const IS_FOCUS = ['==', ['get', 'focus'], true];

export function addSimLayers(map) {
  for (const name of [...LIVE_SOURCES, ...OVERLAY_SOURCES]) map.addSource(`sim-${name}`, {type: 'geojson', data: empty()});
  map.addImage('hub-diamond', diamondIcon(), {sdf: true});
  map.addImage('chain-arrow', arrowIcon(), {sdf: true});
  map.addImage('focus-square', squareIcon(), {sdf: true});
  map.addLayer({id: 'sim-risk', type: 'fill', source: 'sim-risk'}, 'admin-fill');
  // Zone fills sit under the heat zone, so the heat field is never veiled.
  map.addLayer({id: 'sim-zones-fill', type: 'fill', source: 'sim-zones', paint: {'fill-opacity': ZONE_FILL_OPACITY}});
  // Kernel radius and intensity grow with zoom so the field reads as a zone
  // at state scale and still separates neighbouring farms close up.
  map.addLayer({id: 'sim-heat', type: 'heatmap', source: 'sim-heat', paint: {
    'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 6, 14, 9, 30, 12, 60],
    'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 6, .8, 12, 2],
    'heatmap-opacity': .8, 'heatmap-weight': 0,
  }});
  map.addLayer({id: 'sim-interventions-fill', type: 'fill', source: 'sim-interventions', paint: {'fill-opacity': .07}});
  map.addLayer({id: 'sim-interventions-line', type: 'line', source: 'sim-interventions', paint: {'line-width': 1.5, 'line-dasharray': [3, 2]}});
  // Ring boundaries (exterior rings only, see zoneEdges), under trucks, chain and farms.
  // Standstill: a wide translucent band on the protection boundary, the line trucks may not cross.
  map.addLayer({id: 'sim-zones-standstill', type: 'line', source: 'sim-zones-edge',
    filter: ['all', IS_RING('protecao'), ['==', ['get', 'standstill'], true]],
    layout: {'line-join': 'round'}, paint: {'line-width': 7, 'line-opacity': .3}});
  map.addLayer({id: 'sim-zones-casing', type: 'line', source: 'sim-zones-edge',
    layout: {'line-join': 'round'}, paint: {'line-width': 4, 'line-opacity': .6}});
  for (const ring of ZONE_RINGS) {
    map.addLayer({id: `sim-zones-${ring.key}`, type: 'line', source: 'sim-zones-edge', filter: IS_RING(ring.key),
      layout: {'line-join': 'round'}, paint: ZONE_LINES[ring.key]});
  }
  map.addLayer({id: 'sim-trucks-intro', type: 'line', source: 'sim-movements', filter: ['==', ['get', 'introduced_infection'], true],
    layout: {'line-cap': 'round'}, paint: {'line-width': 10, 'line-opacity': .45}});
  map.addLayer({id: 'sim-trucks', type: 'line', source: 'sim-movements', layout: {'line-cap': 'round', 'line-join': 'round'},
    paint: {'line-width': ['case', ['get', 'introduced_infection'], 4.5, 2], 'line-opacity': .9}});
  map.addLayer({id: 'sim-birds', type: 'circle', source: 'sim-birds',
    paint: {'circle-radius': ['match', ['get', 'state'], 'I', 4, 2.5], 'circle-stroke-width': ['match', ['get', 'state'], 'I', 1, 0]}});
  map.addLayer({id: 'sim-flocks-halo', type: 'circle', source: 'sim-flocks', paint: {
    'circle-radius': FLOCK_RADIUS, 'circle-opacity': 0, 'circle-stroke-width': ['case', ['get', 'infected'], 5, 3.5], 'circle-stroke-opacity': FLOCK_FADE}});
  map.addLayer({id: 'sim-flocks', type: 'circle', source: 'sim-flocks', paint: {
    'circle-radius': FLOCK_RADIUS, 'circle-opacity': 0, 'circle-stroke-width': ['case', ['get', 'infected'], 2.5, 1.5], 'circle-stroke-opacity': FLOCK_FADE}});
  // Flocks that landed at a documented site: a dot at the ring's centre.
  map.addLayer({id: 'sim-flocks-site', type: 'circle', source: 'sim-flocks', filter: HAS_SITE, paint: {
    'circle-radius': 3, 'circle-opacity': FLOCK_FADE, 'circle-stroke-width': 1, 'circle-stroke-opacity': FLOCK_FADE}});
  // Transmission chain: a surface-coloured casing keeps the pathway hues legible over the heat zone.
  map.addLayer({id: 'sim-chain-casing', type: 'line', source: 'sim-chain', filter: IS_LINE,
    layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {'line-width': CHAIN_CASING_WIDTH, 'line-opacity': .8}});
  map.addLayer({id: 'sim-chain', type: 'line', source: 'sim-chain', filter: ['all', IS_LINE, ['!=', ['get', 'pathway'], 'local']],
    layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {'line-width': CHAIN_WIDTH}});
  // Neighbourhood links are a "most likely source" attribution, so they are dashed.
  map.addLayer({id: 'sim-chain-local', type: 'line', source: 'sim-chain', filter: ['all', IS_LINE, ['==', ['get', 'pathway'], 'local']],
    layout: {'line-join': 'round'}, paint: {'line-width': CHAIN_WIDTH, 'line-dasharray': [2, 1.5]}});
  map.addLayer({id: 'sim-chain-arrows', type: 'symbol', source: 'sim-chain', filter: IS_LINE, layout: {
    'symbol-placement': 'line', 'symbol-spacing': 72, 'icon-image': 'chain-arrow',
    'icon-size': ['interpolate', ['linear'], ['get', 'weight'], 0, .55, 1, .8],
    'icon-rotation-alignment': 'map', 'icon-keep-upright': false, 'icon-allow-overlap': true, 'icon-ignore-placement': true,
  }, paint: {'icon-halo-width': 1.5}});
  // Introductions without a path (initial seeding, bird position unknown): a ring on the farm.
  map.addLayer({id: 'sim-chain-seed', type: 'circle', source: 'sim-chain', filter: ['==', ['geometry-type'], 'Point'],
    paint: {'circle-radius': 10, 'circle-opacity': 0, 'circle-stroke-width': 2}});
  // Farms carry a second cue besides hue (size, ring, hollow) because six
  // status hues cannot all stay distinguishable as map dots for every reader.
  // Restocked farms keep the culled violet as their ring: birds again, after a cull.
  map.addLayer({id: 'sim-farms', type: 'circle', source: 'sim-farms',
    layout: {'circle-sort-key': byStatus({healthy: 0, quarantined: 1, culled: 1, restocked: 1, hidden: 2}, 3)},
    paint: {
      'circle-radius': byStatus({healthy: 4.5, quarantined: 5.5, hidden: 6.5, notified: 7}, 5),
      'circle-opacity': byStatus({culled: 0}, 1),
      'circle-stroke-width': byStatus({quarantined: 2.5, notified: 2.5, culled: 2.5, restocked: 2}, 1.5),
    }});
  // Production type is a shape cue, so status keeps the colour: laying farms get a surface-coloured core.
  map.addLayer({id: 'sim-farms-type', type: 'circle', source: 'sim-farms', filter: ['==', ['get', 'production'], 'postura'],
    paint: {'circle-radius': 1.75}});
  map.addLayer({id: 'sim-focus', type: 'symbol', source: 'sim-farms', filter: IS_FOCUS, layout: {
    'icon-image': 'focus-square', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
  }, paint: {'icon-halo-width': 1.5}});
  // Hubs that relayed fomite in the chain window: a ring behind the diamond, wider with more introductions.
  map.addLayer({id: 'sim-chain-hubs', type: 'circle', source: 'sim-chain-hubs', paint: {
    'circle-radius': ['interpolate', ['linear'], ['get', 'introductions'], 1, 13, 10, 20], 'circle-opacity': 0, 'circle-stroke-width': 2.5}});
  map.addLayer({id: 'sim-hubs', type: 'symbol', source: 'sim-hubs', layout: {
    'icon-image': 'hub-diamond', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
    'icon-size': ['interpolate', ['linear'], ['get', 'load'], 0, .75, 1, 1.3],
  }, paint: {'icon-halo-width': 2}});
  map.addLayer({id: 'sim-selected', type: 'circle', source: 'sim-farms', filter: ['==', ['get', 'id'], ''],
    paint: {'circle-radius': 13, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-width': 2.5}});
}

/** Trailing mean of Rt by day; days without RT_AVERAGE_DAYS finite values in their window are left out. */
function rtAverages(rows) {
  const averages = new Map();
  rows.forEach((row, i) => {
    const window = rows.slice(Math.max(0, i - RT_AVERAGE_DAYS + 1), i + 1)
      .filter(other => other.day > row.day - RT_AVERAGE_DAYS && Number.isFinite(other.rt));
    if (window.length === RT_AVERAGE_DAYS) averages.set(row.day, window.reduce((sum, other) => sum + other.rt, 0) / RT_AVERAGE_DAYS);
  });
  return averages;
}

const NO_ZONES = {key: null, day: null, foci: 0, standstill: 0, radii: {}};

/** Ring boundaries as lines: only each polygon's exterior ring, so an annulus
 *  does not redraw the inner ring's boundary with its own dash. */
function zoneEdges(collection) {
  const exteriors = ({type, coordinates}) => type === 'Polygon' ? [coordinates[0]]
    : type === 'MultiPolygon' ? coordinates.map(polygon => polygon[0]) : [];
  return {type: 'FeatureCollection', features: collection.features.flatMap(feature => exteriors(feature.geometry)
    .map(ring => ({type: 'Feature', properties: feature.properties, geometry: {type: 'LineString', coordinates: ring}})))};
}

const hubName = (hub) => hub.name || `${kindLabel(hub.kind)} ${hub.id}`;
const pathwayLabel = (key) => PATHWAYS.find(p => p.transmission === key)?.label ?? (key === 'initial' ? 'Introdução inicial' : key);
const plural = (count, one, many) => `${fmt.int(count)} ${count === 1 ? one : many}`;

// ---------------------------------------------------------------- simulation controller

export function createSim(map, {onLayersChanged, isActive}) {
  const sim = {
    state: null, history: [], params: null,
    viewDay: null,                 // null = live; otherwise a replayed snapshot day
    farms: new Map(),              // id → feature, for the day on screen
    hubs: new Map(),               // id → feature, for the day on screen
    selected: null, detail: null,
    events: [], next: 0, skipped: 0, dropped: 0,
    filters: new Set(EVENT_CATEGORIES.filter(c => c.on).map(c => c.key)),
    running: false, busy: false,
    riskMode: 'total', chartScope: 'all',
    farmType: '',                  // '' = every farm, else a PRODUCTIONS key
    arrivalProfile: null,          // monthly migratory arrival profile, from /api/sim/flocks
  };
  let replayAbort, detailAbort, scrubTimer;
  const replaying = () => sim.viewDay !== null;
  // The official transit protocol is a run-level switch; older servers omit the block.
  const protocolOn = () => Boolean(sim.state?.protocol?.enabled);
  const stepDays = () => Number(document.querySelector('input[name=step-days]:checked').value);

  // ------------------------------------------------------------ paint (theme-aware)
  function paint() {
    const t = token, set = (layer, property, value) => map.setPaintProperty(layer, property, value);
    const status = Object.fromEntries(STATUSES.map(s => [s.key, t(`st-${s.key}`)]));
    set('sim-farms', 'circle-color', byStatus({healthy: status.healthy, quarantined: status.quarantined, hidden: status.hidden, notified: status.notified, restocked: status.restocked}, status.culled));
    set('sim-farms', 'circle-stroke-color', byStatus({quarantined: t('ink'), notified: t('ink'), culled: status.culled, restocked: status.culled}, t('map-ring')));
    set('sim-farms-type', 'circle-color', t('map-ring'));
    set('sim-selected', 'circle-stroke-color', t('ink'));
    const fomite = (property) => ['interpolate', ['linear'], ['get', property], 0, t('fomite-0'), .5, t('fomite-1'), 1, t('fomite-2')];
    set('sim-hubs', 'icon-color', fomite('load'));
    set('sim-hubs', 'icon-halo-color', t('map-ring'));
    set('sim-trucks', 'line-color', fomite('load_on_arrival'));
    set('sim-trucks-intro', 'line-color', status.hidden);
    set('sim-birds', 'circle-color', ['match', ['get', 'state'], 'I', t('pw-birds'), 'R', t('bird-r'), t('bird-s')]);
    set('sim-birds', 'circle-stroke-color', t('map-ring'));
    set('sim-interventions-fill', 'fill-color', status.culled);
    set('sim-interventions-line', 'line-color', status.culled);
    set('sim-flocks', 'circle-stroke-color', ['case', ['get', 'infected'], t('pw-birds'), t('bird-s')]);
    set('sim-flocks-halo', 'circle-stroke-color', t('map-ring'));
    set('sim-flocks-site', 'circle-color', ['case', ['get', 'infected'], t('pw-birds'), t('bird-s')]);
    set('sim-flocks-site', 'circle-stroke-color', t('map-ring'));
    // Initial seedings have no pathway hue: they take secondary ink, as in the timeline.
    const pathway = ['match', ['get', 'pathway'], ...PATHWAYS.flatMap(p => [p.transmission, t(`pw-${p.key}`)]), t('ink-2')];
    set('sim-chain-casing', 'line-color', t('map-ring'));
    set('sim-chain', 'line-color', pathway);
    set('sim-chain-local', 'line-color', pathway);
    set('sim-chain-arrows', 'icon-color', pathway);
    set('sim-chain-arrows', 'icon-halo-color', t('map-ring'));
    set('sim-chain-seed', 'circle-stroke-color', pathway);
    set('sim-chain-hubs', 'circle-stroke-color', t('pw-fomite'));
    set('sim-zones-fill', 'fill-color', t('zone'));
    set('sim-zones-standstill', 'line-color', t('zone'));
    set('sim-zones-casing', 'line-color', t('map-ring'));
    for (const ring of ZONE_RINGS) set(`sim-zones-${ring.key}`, 'line-color', t('zone'));
    set('sim-focus', 'icon-color', t('zone'));
    set('sim-focus', 'icon-halo-color', t('map-ring'));
    const heat = [1, 2, 3, 4].map(step => t(`heat-${step}`));
    const clear = `rgba(${[1, 3, 5].map(i => parseInt(heat[0].slice(i, i + 2), 16)).join(', ')}, 0)`;
    set('sim-heat', 'heatmap-color', ['interpolate', ['linear'], ['heatmap-density'], 0, clear, .15, heat[0], .4, heat[1], .7, heat[2], 1, heat[3]]);
    paintRisk();
    paintHeat();
  }
  function paintRisk() {
    const t = token;
    if (sim.riskMode === 'total') {
      map.setPaintProperty('sim-risk', 'fill-color', ['match', ['get', 'level'], 'red', t('risk-3'), 'yellow', t('risk-2'), t('risk-1')]);
      map.setPaintProperty('sim-risk', 'fill-opacity', .55);
    } else {
      map.setPaintProperty('sim-risk', 'fill-color', ['case',
        ['all', ['>=', ['get', 'bird'], ['get', 'fomite']], ['>=', ['get', 'bird'], ['get', 'local']]], t('pw-birds'),
        ['>=', ['get', 'fomite'], ['get', 'local']], t('pw-fomite'), t('pw-local')]);
      map.setPaintProperty('sim-risk', 'fill-opacity', ['match', ['get', 'level'], 'red', .7, 'yellow', .5, .3]);
    }
    $('legend-risk-total').hidden = sim.riskMode !== 'total';
    $('legend-risk-dominant').hidden = sim.riskMode === 'total';
  }

  // ------------------------------------------------------------ data flow
  function setCollections(names, collections) {
    names.forEach((name, i) => map.getSource(`sim-${name}`).setData(collections[i]));
    const byId = (name) => new Map(collections[names.indexOf(name)].features.map(feature => [feature.properties.id, feature]));
    sim.farms = byId('farms');
    sim.hubs = byId('hubs');
  }

  async function refreshLive() {
    const [state, history, ...collections] = await Promise.all([
      get('/api/sim/state'), get('/api/sim/history'), ...LIVE_SOURCES.map(name => get(`/api/sim/${name}`)),
    ]);
    replayAbort?.abort();
    patchToday(history, state, collections[LIVE_SOURCES.indexOf('farms')]);
    sim.state = state; sim.history = history; sim.viewDay = null;
    setCollections(LIVE_SOURCES, collections);
    renderAll();
    onLayersChanged();
    const risk = collections[LIVE_SOURCES.indexOf('risk')];
    if (isActive()) status(risk.truncated ? 'Camada de risco limitada: muitas células com risco.' : `Dia ${state.day} pronto.`);
    // Panels fetch independently so one failing endpoint does not stall playback.
    await Promise.all([fetchEvents(), sim.selected ? loadDetail() : null, refreshOverlays()]);
  }

  /** Today's history row is written at step time; interventions made since then
   *  already show in state and farms, so today's row is updated from them. */
  function patchToday(history, state, farms) {
    const last = history.at(-1);
    if (!last || last.day !== state.day) return;
    const patch = {rt: state.rt, cost: state.cumulative_cost, deaths: state.total_deaths,
      culled_birds: state.total_culled, infected_birds: state.infected_birds, birds: state.birds, lots: state.lots};
    // A truncated collection cannot be counted; keep the step-time counts then.
    if (!farms.truncated) {
      for (const s of STATUSES) patch[s.key] = 0;
      for (const feature of farms.features) patch[feature.properties.status] += 1;
    }
    history[history.length - 1] = {...last, ...patch};
  }

  async function simAction(action) {
    if (sim.busy) return null;
    sim.busy = true;
    document.body.classList.add('sim-busy');
    try { await action(); await refreshLive(); return true; }
    catch (error) { setRunning(false); status(error.message, 'error'); return false; }
    finally { sim.busy = false; document.body.classList.remove('sim-busy'); }
  }

  const step = () => {
    // A pending scrub must not pull the view back into replay after this step.
    clearTimeout(scrubTimer);
    return simAction(() => post('/api/sim/step', {days: stepDays()}));
  };

  function setRunning(running) {
    sim.running = running;
    $('sim-run').textContent = running ? 'Pausar' : 'Rodar';
    $('sim-run').setAttribute('aria-pressed', String(running));
    $('sim-step').disabled = running;
  }
  async function runLoop() {
    while (sim.running) {
      if (await step() === false) break;
      await sleep(PAUSE_MS[$('sim-speed').value]);
    }
  }
  function toggleRun() {
    setRunning(!sim.running);
    if (sim.running) runLoop();
  }

  // ------------------------------------------------------------ replay
  async function loadDay(day) {
    replayAbort?.abort();
    const controller = replayAbort = new AbortController();
    try {
      status(`Carregando o dia ${day}…`, 'busy');
      const collections = await Promise.all(REPLAY_SOURCES.map(name => get(`/api/sim/${name}?day=${day}`, {signal: controller.signal})));
      sim.viewDay = day;
      setCollections(REPLAY_SOURCES, collections);
      renderAll();
      onLayersChanged();
      status(`Revendo o dia ${day}. A camada de risco existe só para o dia atual.`);
      await refreshOverlays();
    } catch (error) {
      if (!isAbort(error)) status(error.message, 'error');
    }
  }
  function jumpTo(day) {
    const [first, last] = sim.state.snapshot_days;
    const target = Math.max(first, Math.min(last, day));
    setRunning(false);
    if (target === last) { if (replaying()) goLive(); return; }
    $('scrub').value = target;
    loadDay(target);
  }
  function goLive() {
    clearTimeout(scrubTimer);
    replayAbort?.abort();
    return simAction(async () => {});
  }

  // ------------------------------------------------------------ rendering
  const rowFor = (day) => sim.history.findLast(row => row.day === day);
  const shownDay = () => sim.viewDay ?? sim.state.day;
  /** Calendar date of a simulated day, from today's `date`; null when the server sends none. */
  function dateOf(day) {
    const today = Date.parse(`${sim.state.date}T00:00:00Z`);
    return Number.isFinite(today) ? new Date(today + (day - sim.state.day) * DAY_MS) : null;
  }
  const dateText = (day) => { const date = dateOf(day); return date ? DATE_FORMAT.format(date) : ''; };

  function renderAll() {
    renderPlayback();
    renderKpis();
    renderCharts();
    renderTable();
    renderTimeline();
    renderArrivalProfile();
    if (sim.selected) renderCard();
  }

  function renderPlayback() {
    const [first, last] = sim.state.snapshot_days;
    const scrub = $('scrub');
    scrub.min = first; scrub.max = last;
    scrub.value = shownDay();
    scrub.disabled = first === last;
    scrub.setAttribute('aria-valuetext', `Dia ${shownDay()}${replaying() ? ' (replay)' : ' (ao vivo)'}`);
    $('scrub-first').textContent = `dia ${first}`;
    $('scrub-last').textContent = `dia ${last}`;
    $('playback-day').textContent = `Dia ${shownDay()}`;
    $('playback-date').textContent = dateText(shownDay());
    $('playback-mode').textContent = replaying() ? `revendo; ao vivo é o dia ${last}` : 'ao vivo';
    $('playback-mode').dataset.state = replaying() ? 'replay' : 'live';
    $('go-live').hidden = !replaying();
    $('risk-replay-note').hidden = !replaying();
    document.body.classList.toggle('replaying', replaying());
    const s = sim.state;
    const notes = [];
    if (s.synthetic_facilities) {
      notes.push(h('span', {}, 'Granjas sintéticas, não reais: municípios sorteados por dados oficiais '
        + '(IBGE PPM 2024, POG 1º tri 2025, ADAGRO); número e porte das granjas de corte e posição das bases são suposições. Fontes em ', h('code', {}, 'docs/FONTES_DADOS.md'), '.'));
    }
    if (!s.risk_available) notes.push('Risco indisponível: o mundo não tem raster ambiental.');
    if (protocolOn()) notes.push('Protocolo oficial de trânsito (MAPA) ativo; as suposições estão marcadas em Cenário e parâmetros.');
    notes.push(`${fmt.int(s.farms)} granjas${farmKindsText(s.farms_by_kind)}, ${fmt.int(s.trucks)} caminhões em ${fmt.int(s.hubs)} bases, semente ${s.seed}.`);
    $('sim-notes').replaceChildren(...notes.map(note => h('li', {}, note)));
    for (const input of document.querySelectorAll('input[name=risk-mode]')) input.disabled = !s.risk_available;
    renderProtocol();
  }

  /** " (205 de corte, 62 de postura)" from `farms_by_kind`; other kinds by their label. */
  function farmKindsText(byKind) {
    const parts = Object.entries(byKind ?? {}).map(([kind, count]) => {
      const production = PRODUCTIONS.find(p => p.kind === kind);
      return `${fmt.int(count)} ${production ? production.plural : kindLabel(kind).toLowerCase()}`;
    });
    return parts.length ? ` (${parts.join(', ')})` : '';
  }

  /** Zones layer, focus legend and KPI tile exist only while the protocol is on. */
  function renderProtocol() {
    const on = protocolOn();
    for (const id of ['zones-layer', 'legend-focus', 'kpi-protocol']) $(id).hidden = !on;
    if (!on && overlay.zones.key !== null) clearOverlay('zones');
    renderZones();
  }

  function renderKpis() {
    const row = rowFor(shownDay());
    if (!row) return;
    const prev = rowFor(row.day - 1);
    const total = STATUSES.reduce((sum, s) => sum + row[s.key], 0);
    const put = (id, value, note) => {
      $(id).querySelector('.kpi-value').textContent = value;
      $(id).querySelector('.kpi-note').textContent = note;
    };
    const date = dateOf(row.day);
    put('kpi-day', String(row.day), [date ? SHORT_DATE_FORMAT.format(date) : null, replaying() ? 'replay' : 'ao vivo'].filter(Boolean).join(' · '));
    const rt = $('kpi-rt');
    const trend = prev && Number.isFinite(prev.rt) ? Math.sign(row.rt - prev.rt) : 0;
    rt.querySelector('.kpi-value').textContent = fmt.dec(row.rt);
    rt.querySelector('.kpi-trend').textContent = trend > 0 ? '▲' : trend < 0 ? '▼' : '';
    rt.querySelector('.kpi-trend').setAttribute('aria-label', trend > 0 ? 'subindo' : trend < 0 ? 'caindo' : 'estável');
    rt.querySelector('.kpi-note').textContent = row.rt >= 1 ? 'em expansão' : 'em retração';
    rt.dataset.level = row.rt >= 1 ? 'up' : 'down';
    for (const s of STATUSES) {
      put(`kpi-${s.key}`, fmt.int(row[s.key]), total ? `${fmt.num(row[s.key] / total * 100, 1)}% das granjas` : '');
    }
    if (!replaying()) $('kpi-quarantined').querySelector('.kpi-note').textContent = `total ativo: ${fmt.int(sim.state.active_quarantines)}`;
    put('kpi-deaths', fmt.compact(row.deaths), `${fmt.compact(row.culled_birds)} sacrificadas`);
    put('kpi-cost', fmt.moneyCompact(row.cost), fmt.moneyCompact(row.cost) === fmt.money(row.cost) ? '' : fmt.money(row.cost));
    put('kpi-birds', fmt.int(row.infected_birds), `de ${fmt.int(row.birds)} aves`);
    segmentedBar($('status-ribbon'), STATUSES.map(s => ({label: s.plural, value: row[s.key], color: `st-${s.key}`, text: fmt.int(row[s.key])})),
      `Granjas por situação no dia ${row.day}`);
    renderProtocolKpi();
    renderMarkets();
  }

  /** Live counts come from the state; a replayed day only has them through that day's zones. */
  function renderProtocolKpi() {
    const tile = $('kpi-protocol');
    if (tile.hidden) return;
    const protocol = sim.state.protocol;
    const counts = !replaying() ? {foci: protocol.active_foci, standstill: protocol.standstill_foci}
      : zonesShown() ? overlay.zones : null;
    tile.querySelector('.kpi-value').textContent = counts ? fmt.int(counts.foci) : '—';
    tile.querySelector('.kpi-note').textContent = !counts ? 'ligue as zonas'
      : `${fmt.int(counts.standstill)} ${counts.standstill === 1 ? 'interdição' : 'interdições'} de trânsito`;
    const inZones = !replaying() ? protocol.farms_in_zones : null;
    tile.title = inZones ? `Granjas nas zonas: ${ZONE_RINGS.map(r => `${r.label.toLowerCase()} ${fmt.int(inZones[r.key])}`).join(', ')}`
      : replaying() && !counts ? 'No replay, os focos vêm da camada Zonas de emergência.' : '';
  }

  function renderCharts() {
    const rows = sim.history, markDay = sim.viewDay, onPick = jumpTo;
    const statuses = sim.chartScope === 'all' ? STATUSES : STATUSES.filter(s => s.key !== 'healthy');
    timeChart($('chart-status'), $('readout-status'), {
      rows, kind: 'stack', markDay, onPick, label: 'Granjas por situação', format: fmt.int, tick: fmt.compact,
      series: statuses.map(s => ({label: s.plural.toLowerCase(), color: `st-${s.key}`, value: row => row[s.key]})),
    });
    const rtAverage = rtAverages(rows);
    timeChart($('chart-rt'), $('readout-rt'), {
      rows, kind: 'line', markDay, onPick, label: 'Número reprodutivo Rt', format: fmt.dec, tick: v => fmt.num(v, 1),
      refLine: {value: 1, label: 'Rt = 1'}, series: [
        {label: 'Rt do dia', color: 'series-neutral', faint: true, value: row => row.rt},
        {label: `média móvel ${RT_AVERAGE_DAYS} dias`, color: 'series-neutral', value: row => rtAverage.get(row.day) ?? NaN},
      ],
    });
    timeChart($('chart-incidence'), $('readout-incidence'), {
      rows, kind: 'bars', markDay, onPick, label: 'Incidência diária de aves expostas', format: v => fmt.num(v, 1), tick: fmt.compact,
      series: [{label: 'aves expostas no dia', color: 'st-hidden', value: row => row.new_exposed}],
    });
    timeChart($('chart-intro'), $('readout-intro'), {
      rows, kind: 'bars', markDay, onPick, label: 'Introduções em granjas por via', format: fmt.int, tick: fmt.int,
      series: PATHWAYS.map(p => ({label: p.label.toLowerCase(), color: `pw-${p.key}`, value: row => row.introductions[p.event]})),
    });
    // Lot tallies are cumulative; both products add up as lots (their quantities have different units).
    const lots = (tally) => (row) => row.lots ? PRODUCTS.reduce((sum, p) => sum + (row.lots[p.key]?.[tally]?.lots ?? 0), 0) : NaN;
    timeChart($('chart-lots'), $('readout-lots'), {
      rows, kind: 'line', markDay, onPick, label: 'Lotes expostos e recolhidos, acumulado', format: fmt.int, tick: fmt.compact,
      series: [
        {label: 'lotes expostos', color: 'st-hidden', value: lots('exposed')},
        {label: 'expostos recolhidos', color: 'series-neutral', value: lots('exposed_recalled')},
      ],
    });
    timeChart($('chart-cost'), $('readout-cost'), {
      rows, kind: 'line', markDay, onPick, label: 'Custo acumulado', format: fmt.money, tick: fmt.moneyCompact,
      series: [{label: 'custo', color: 'series-neutral', value: row => row.cost}],
    });
    timeChart($('chart-birds'), $('readout-birds'), {
      rows, kind: 'line', markDay, onPick, label: 'Aves silvestres infectadas', format: fmt.int, tick: fmt.compact,
      series: [{label: 'aves infectadas', color: 'pw-birds', value: row => row.infected_birds}],
    });
  }

  function renderTable() {
    const details = $('data-table');
    if (!details.open) return;
    const end = sim.history.findLastIndex(row => row.day <= shownDay());
    const rows = sim.history.slice(Math.max(0, end - TABLE_DAYS + 1), end + 1).reverse();
    const rtAverage = rtAverages(sim.history);
    const columns = [
      ['Dia', row => row.day],
      ...STATUSES.map(s => [s.plural, row => fmt.int(row[s.key])]),
      ['Rt', row => fmt.dec(row.rt)],
      [`Rt média ${RT_AVERAGE_DAYS} dias`, row => fmt.dec(rtAverage.get(row.day))],
      ['Aves expostas', row => fmt.num(row.new_exposed, 1)],
      ...PATHWAYS.map(p => [`Intro. ${p.label.toLowerCase()}`, row => fmt.int(row.introductions[p.event])]),
      ['Viagens', row => fmt.int(row.trips)],
      ['Aves mortas', row => fmt.int(row.deaths)],
      ['Custo', row => fmt.money(row.cost)],
      ['Aves silv. infectadas', row => fmt.int(row.infected_birds)],
      ...PRODUCTS.flatMap(p => [
        [`Lotes expostos (${p.label.toLowerCase()})`, row => fmt.int(row.lots?.[p.key]?.exposed.lots)],
        [`Lotes recolhidos (${p.label.toLowerCase()})`, row => fmt.int(row.lots?.[p.key]?.recalled.lots)],
      ]),
    ];
    $('data-table-body').replaceChildren(h('table', {class: 'grid-table numeric'},
      h('thead', {}, h('tr', {}, columns.map(([name]) => h('th', {scope: 'col'}, name)))),
      h('tbody', {}, rows.map(row => h('tr', {}, columns.map(([, value]) => h('td', {}, String(value(row)))))))));
  }

  // ------------------------------------------------------------ production and markets
  // Cumulative tallies as of the day on screen: the history row when replaying,
  // the state when live (it includes recalls made by interventions since the step).
  const lotsShown = () => replaying() ? rowFor(shownDay())?.lots : sim.state.lots;
  const MARKET_ROWS = [
    ['exposed', 'Expostos', 'saídos de granja infectada ainda não notificada'],
    ['exposed_recalled', 'Expostos recolhidos', null],
    ['exposed_outside_window', 'Expostos fora da janela', 'coletados antes da janela de rastreamento: não recolhidos'],
    ['notified', 'De granja já notificada', 'coletados depois da notificação'],
    ['recalled', 'Recolhidos, total', 'inclui lotes não expostos das granjas rastreadas'],
  ];

  function renderMarkets() {
    const lots = lotsShown();
    if (!lots) {
      $('markets-note').textContent = 'Este servidor não informa lotes de produto.';
      $('markets-table').replaceChildren();
      return;
    }
    const cell = (tally, product) => {
      const {lots: count, quantity} = tally;
      return h('td', {}, fmt.int(count), count ? h('span', {class: 'qty'}, `${fmt.compact(quantity)} ${product.unit}`) : null);
    };
    $('markets-note').textContent = `Lotes coletados por caminhões de entrepostos de ovos e abatedouros, acumulados até o dia ${shownDay()}.`;
    $('markets-table').replaceChildren(h('table', {class: 'grid-table markets-grid'},
      h('thead', {}, h('tr', {}, h('th', {scope: 'col'}, 'Lotes'), PRODUCTS.map(p => h('th', {scope: 'col'}, `${p.label} (${p.unit})`)))),
      h('tbody', {},
        MARKET_ROWS.map(([key, label, note]) => h('tr', {title: note},
          h('th', {scope: 'row'}, label), PRODUCTS.map(p => lots[p.key] ? cell(lots[p.key][key], p) : h('td', {}, '—')))),
        h('tr', {}, h('th', {scope: 'row', title: 'Do dia da coleta ao dia do recolhimento, nos lotes expostos recolhidos'}, 'Exposição média até o recolhimento'),
          PRODUCTS.map(p => {
            const days = lots[p.key]?.mean_exposure_days;
            return h('td', {}, Number.isFinite(days) ? `${fmt.num(days, 1)} dias` : 'sem recolhimento');
          })))));
    renderMarketHubs();
  }

  // Product hubs ranked by exposed lots. /api/sim/lots also returns every lot
  // of the window (hundreds of kB per simulated month), so it is fetched only
  // while the list is open or a hub popup asks, and reused until the run, the
  // day on screen or the live day changes.
  const markets = {key: null, data: null, abort: null};
  const marketsKey = () => `${overlay.run}:${shownDay()}:${sim.state.day}`;

  async function marketHubs() {
    const key = marketsKey();
    if (markets.key === key) return markets.data;
    markets.abort?.abort();
    const controller = markets.abort = new AbortController();
    try {
      const data = await get(`/api/sim/lots?to_day=${shownDay()}`, {signal: controller.signal});
      if (markets.abort !== controller) return null;
      markets.key = key; markets.data = data.hubs;
      return data.hubs;
    } catch (error) {
      if (!isAbort(error)) status(`Lotes: ${error.message}`, 'error');
      return null;
    }
  }

  async function renderMarketHubs() {
    const note = $('markets-hubs-note'), bars = $('markets-hubs-bars');
    if (!$('markets-hubs').open) { markets.abort?.abort(); return; }
    if (markets.key !== marketsKey()) note.textContent = 'Carregando os lotes…';
    const hubs = await marketHubs();
    if (!hubs || !$('markets-hubs').open) return;
    note.textContent = `Lotes coletados até o dia ${shownDay()}, pela base de destino.`
      + (replaying() ? ` Recolhimentos contados até o dia ${sim.state.day} (ao vivo).` : '');
    if (!hubs.length) { bars.replaceChildren(h('li', {class: 'muted'}, 'Nenhum entreposto de ovos ou abatedouro neste cenário.')); return; }
    const top = Math.max(...hubs.map(hub => hub.exposed_lots), 1);
    bars.replaceChildren(...hubs.map(hub => {
      const text = `${hubName(hub)}: ${plural(hub.exposed_lots, 'lote exposto', 'lotes expostos')} de ${fmt.int(hub.lots)}, `
        + `${plural(hub.recalled_lots, 'recolhido', 'recolhidos')}, ${fmt.int(hub.outside_window_lots)} fora da janela`;
      return h('li', {}, h('button', {type: 'button', class: 'rank-bar', title: text, 'aria-label': `${text}. Mostrar no mapa.`, onclick: () => flyToHub(hub)},
        h('span', {class: 'rank-name'}, hubName(hub)),
        h('span', {class: 'rank-track'}, h('span', {class: 'rank-fill', style: `width: ${hub.exposed_lots / top * 100}%`})),
        h('span', {class: 'rank-value'}, fmt.int(hub.exposed_lots))));
    }));
  }

  // ------------------------------------------------------------ events timeline
  async function fetchEvents() {
    try {
      const log = await get(`/api/sim/events?since=${sim.next}`);
      sim.next = log.next;
      sim.skipped += log.skipped;
      sim.events.push(...log.events);
      if (sim.events.length > MAX_EVENTS_KEPT) {
        sim.dropped += sim.events.length - MAX_EVENTS_KEPT;
        sim.events.splice(0, sim.events.length - MAX_EVENTS_KEPT);
      }
      renderTimeline();
    } catch (error) {
      status(`Eventos: ${error.message}`, 'error');
    }
  }
  function resetEvents() {
    sim.events = []; sim.next = 0; sim.skipped = 0; sim.dropped = 0;
  }

  const eventAccent = (type) => ({
    INTRODUCAO_FOMITE: 'pw-fomite', INTRODUCAO_VIZINHANCA: 'pw-local', INTRODUCAO_AVES_MIGRATORIAS: 'pw-birds',
    INTRODUCAO_INICIAL: 'ink-2', DETECCAO: 'st-notified', REACAO_AUTOMATICA: 'st-quarantined',
    QUARENTENA_PERIFERICA: 'st-quarantined', LIBERACAO_QUARENTENA: 'st-healthy', SACRIFICIO_SANITARIO: 'st-culled',
    BLOQUEIO_MOVIMENTACAO: 'st-quarantined', MELHORIA_BIOSSEGURANCA: 'st-healthy', INFECCAO_AVES_SILVESTRES: 'pw-birds',
    CHEGADA_BANDO_INFECTADO: 'pw-birds', INFECCAO: 'st-hidden', FIM_INFECCAO: 'muted',
    FOCO_CONFIRMADO: 'zone', DESPOVOAMENTO: 'st-culled', INTERDICAO_TRANSITO: 'zone', VIGILANCIA_ATIVA: 'st-notified',
    VAZIO_SANITARIO: 'st-culled', REPOVOAMENTO: 'st-restocked', REPOVOAMENTO_TOTAL: 'st-healthy',
    RECOLHIMENTO_LOTES: 'series-neutral',
  })[type] ?? 'muted';

  function renderTimeline() {
    const counts = new Map();
    for (const event of sim.events) counts.set(eventCategory(event.type), (counts.get(eventCategory(event.type)) ?? 0) + 1);
    $('event-filters').replaceChildren(...EVENT_CATEGORIES.map(c => h('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(sim.filters.has(c.key)),
      onclick: () => { sim.filters.has(c.key) ? sim.filters.delete(c.key) : sim.filters.add(c.key); renderTimeline(); },
    }, c.label, h('span', {class: 'chip-count'}, fmt.int(counts.get(c.key) ?? 0)))));

    const byDay = new Map();
    for (const event of sim.events) {
      if (!sim.filters.has(eventCategory(event.type))) continue;
      if (!byDay.has(event.day)) byDay.set(event.day, []);
      byDay.get(event.day).push(event);
    }
    const groups = [];
    let rendered = 0;
    for (const day of [...byDay.keys()].sort((a, b) => b - a)) {
      if (rendered >= TIMELINE_ROWS) break;
      const events = byDay.get(day);
      // Per-farm daily exposure events are summarised: one line per day.
      const exposures = events.filter(e => e.type === 'INFECCAO');
      const items = events.filter(e => e.type !== 'INFECCAO').reverse().map(eventRow);
      if (exposures.length) {
        items.push(h('li', {class: 'event', style: `--c: var(--${eventAccent('INFECCAO')})`},
          h('span', {class: 'event-type'}, eventLabel('INFECCAO')),
          h('span', {class: 'event-detail'}, `${fmt.int(new Set(exposures.map(e => e.farm_id)).size)} granjas com novas aves expostas`)));
      }
      rendered += items.length;
      groups.push(h('li', {class: `event-day${day === sim.viewDay ? ' current' : ''}`},
        h('h3', {}, h('button', {type: 'button', class: 'day-link', onclick: () => jumpTo(day), title: 'Rever este dia no mapa'}, `Dia ${day}`)),
        h('ul', {class: 'event-list'}, items)));
    }
    const notes = [];
    if (sim.skipped) notes.push(`${fmt.int(sim.skipped)} eventos antigos omitidos pelo servidor (passos longos geram muitos eventos).`);
    if (sim.dropped) notes.push(`${fmt.int(sim.dropped)} eventos antigos descartados da memória.`);
    if (rendered >= TIMELINE_ROWS) notes.push(`Mostrando os ${TIMELINE_ROWS} eventos mais recentes.`);
    $('event-notes').replaceChildren(...notes.map(note => h('p', {}, note)));
    $('events').replaceChildren(...(groups.length ? groups : [h('li', {class: 'muted empty'},
      sim.events.length ? 'Nenhum evento nas categorias selecionadas.' : 'Nenhum evento ainda. Avance a simulação.')]));
  }

  function eventRow(event) {
    const body = [h('span', {class: 'event-type'}, eventLabel(event.type)), h('span', {class: 'event-detail'}, event.details)];
    const style = `--c: var(--${eventAccent(event.type)})`;
    if (!event.farm_id) return h('li', {class: 'event', style}, ...body);
    return h('li', {class: 'event', style}, h('button', {type: 'button', class: 'event-link',
      onclick: () => selectFarm(event.farm_id, {fly: true}), title: 'Mostrar a granja no mapa'}, ...body));
  }

  // ------------------------------------------------------------ overlays: heat zone, transmission chain, flocks
  // Each fetches only while its layer is checked: after the day on screen
  // changes, or when its own controls change. Each has its own abort controller.
  const overlay = {
    abort: {}, run: 0,             // run: bumped on reset, invalidates the chain cache
    heat: null,                    // last /api/sim/heat response
    chain: {run: -1, from: null, to: null, items: [], hubs: []},
    zones: NO_ZONES,
  };
  const OVERLAY_TOGGLES = {heat: 'simHeat', chain: 'simChain', flocks: 'simFlocks', zones: 'simZones'};
  const OVERLAY_NAMES = {heat: 'Zona de calor', chain: 'Cadeia de transmissão', flocks: 'Bandos migratórios', zones: 'Zonas de emergência'};
  const toggle = (name) => document.querySelector(`input[data-layer="${OVERLAY_TOGGLES[name]}"]`);
  const overlayOn = (name) => Boolean(sim.state) && toggle(name).checked && (name !== 'zones' || protocolOn());
  const cancelOverlay = (name) => overlay.abort[name]?.abort();
  const refreshOverlays = () => Promise.all([refreshHeat(), refreshChain(), refreshFlocks(), refreshZones()]);
  const zonesKey = () => `${overlay.run}:${shownDay()}`;
  /** The zones source holds this run's zones for the day on screen. */
  const zonesShown = () => Boolean(sim.state) && overlay.zones.key === zonesKey();

  async function fetchOverlay(name, url) {
    cancelOverlay(name);
    const controller = overlay.abort[name] = new AbortController();
    try {
      const data = await get(url, {signal: controller.signal});
      return overlay.abort[name] === controller ? data : null;
    } catch (error) {
      if (!isAbort(error)) status(`${OVERLAY_NAMES[name]}: ${error.message}`, 'error');
      return null;
    }
  }

  function clearOverlay(name) {
    cancelOverlay(name);
    const sources = {heat: ['heat'], chain: ['chain', 'chain-hubs'], flocks: ['flocks'], zones: ['zones', 'zones-edge']}[name];
    for (const source of sources) map.getSource(`sim-${source}`).setData(empty());
    if (name === 'heat') { overlay.heat = null; paintHeat(); }
    if (name === 'chain') renderHubChain();
    if (name === 'zones') { overlay.zones = NO_ZONES; renderZones(); renderProtocolKpi(); }
  }

  const HEAT_WEIGHTS = {
    introductions: {unit: 'introduções', format: fmt.int},
    infected_days: {unit: 'dias infectada', format: fmt.int},
    exposed: {unit: 'aves expostas', format: fmt.compact},
  };

  async function refreshHeat() {
    if (!overlayOn('heat')) { cancelOverlay('heat'); return; }
    const day = shownDay();
    const query = {
      all: '',
      recent: `from_day=${Math.max(0, day - HEAT_RECENT_DAYS + 1)}&to_day=${day}`,
      until: `to_day=${day}`,
    }[$('heat-window').value];
    const data = await fetchOverlay('heat', `/api/sim/heat?${query}`);
    if (!data) return;
    overlay.heat = data;
    map.getSource('sim-heat').setData(data);
    paintHeat();
  }

  function paintHeat() {
    const key = $('heat-weight').value, weight = HEAT_WEIGHTS[key];
    const features = overlay.heat?.features ?? [];
    const max = features.reduce((top, feature) => Math.max(top, Number(feature.properties[key]) || 0), 0);
    // Each weight is scaled by its own maximum, so introductions, days and
    // exposed birds (orders of magnitude apart) share one ramp.
    map.setPaintProperty('sim-heat', 'heatmap-weight', max > 0 ? ['/', ['to-number', ['get', key], 0], max] : 0);
    const window = overlay.heat?.window;
    const days = window ? `dias ${window[0]} a ${window[1]}` : 'nenhum dia retido no período';
    $('heat-scale').textContent = !overlay.heat ? 'Intensidade relativa à granja com o maior valor.'
      : max > 0 ? `Relativa à granja com mais ${weight.unit}: ${weight.format(max)} (${days}).`
        : `Nenhuma granja com ${weight.unit} (${days}).`;
  }

  async function refreshChain() {
    if (!overlayOn('chain')) { cancelOverlay('chain'); renderHubChain(); return; }
    const to = shownDay();
    const span = Number(document.querySelector('input[name=chain-window]:checked').value);
    const from = span ? Math.max(0, to - span + 1) : 0;
    const cached = overlay.chain;
    // Past days never change within a run, so a window that slides forward
    // only fetches the days it gained ("todos" grows by one step per tick).
    const extend = cached.run === overlay.run && cached.to !== null && cached.to <= sim.state.day
      && from >= cached.from && to >= cached.to && from <= cached.to + 1;
    let fresh = {transmissions: [], hubs: cached.hubs};
    if (!extend || to > cached.to) {
      fresh = await fetchOverlay('chain', `/api/sim/transmissions?from_day=${extend ? cached.to + 1 : from}&to_day=${to}`);
      if (!fresh) return;
    } else {
      cancelOverlay('chain');
    }
    const kept = extend ? cached.items.filter(t => t.day >= from) : [];
    overlay.chain = {run: overlay.run, from, to, items: kept.concat(fresh.transmissions), hubs: fresh.hubs};
    drawChain();
  }

  function drawChain() {
    const features = [];
    for (const {path = [], ...record} of overlay.chain.items) {
      if (!path.length) continue;
      // A path whose points all coincide (initial seeding, bird position unknown) is drawn as a ring.
      const moves = path.some(([lon, lat]) => lon !== path[0][0] || lat !== path[0][1]);
      features.push({type: 'Feature', properties: {...record, weight: Number.isFinite(record.share) ? record.share : 1},
        geometry: moves ? {type: 'LineString', coordinates: path} : {type: 'Point', coordinates: path.at(-1)}});
    }
    map.getSource('sim-chain').setData({type: 'FeatureCollection', features});
    const counts = hubCounts();
    map.getSource('sim-chain-hubs').setData({type: 'FeatureCollection', features: overlay.chain.hubs.filter(hub => counts.has(hub.id)).map(hub => ({
      type: 'Feature', geometry: {type: 'Point', coordinates: [hub.lon, hub.lat]},
      properties: {id: hub.id, introductions: counts.get(hub.id).introductions},
    }))});
    renderHubChain();
  }

  /** Fomite introductions relayed by each hub in the cached window (the API's
   *  `hubs` counts, recounted here because the cache slides incrementally). */
  function hubCounts() {
    const counts = new Map();
    for (const t of overlay.chain.items) {
      if (t.pathway !== 'fomite' || !t.hub_id) continue;
      const entry = counts.get(t.hub_id) ?? {introductions: 0, sources: new Set()};
      entry.introductions += 1;
      if (t.source_farm_id) entry.sources.add(t.source_farm_id);
      counts.set(t.hub_id, entry);
    }
    return counts;
  }

  function renderHubChain() {
    const bars = $('hub-chain-bars'), note = $('hub-chain-note');
    if (!toggle('chain').checked) {
      note.replaceChildren('Bases por onde passaram as introduções por caminhão.',
        h('button', {type: 'button', class: 'link', onclick: showChain}, 'Mostrar a cadeia de transmissão'));
      bars.replaceChildren();
      return;
    }
    const {from, to, hubs} = overlay.chain;
    if (to === null || overlay.chain.run !== overlay.run) { note.textContent = 'Carregando a cadeia de transmissão…'; bars.replaceChildren(); return; }
    const counts = hubCounts();
    const ranked = hubs.filter(hub => counts.has(hub.id)).map(hub => ({hub, ...counts.get(hub.id)}))
      .sort((a, b) => b.introductions - a.introductions || (a.hub.id < b.hub.id ? -1 : 1));
    note.textContent = `Introduções por caminhão repassadas por cada base, dias ${from} a ${to} (janela da cadeia).`;
    if (!ranked.length) {
      bars.replaceChildren(h('li', {class: 'muted'}, 'Nenhuma introdução por caminhão passou por uma base nesta janela.'));
      return;
    }
    const top = ranked[0].introductions;
    const items = ranked.slice(0, HUB_BARS).map(({hub, introductions, sources}) => {
      const text = `${hubName(hub)}: ${plural(introductions, 'introdução', 'introduções')}, ${plural(sources.size, 'granja de origem', 'granjas de origem')}`;
      return h('li', {}, h('button', {type: 'button', class: 'rank-bar', title: text, 'aria-label': `${text}. Mostrar no mapa.`, onclick: () => flyToHub(hub)},
        h('span', {class: 'rank-name'}, hubName(hub)),
        h('span', {class: 'rank-track'}, h('span', {class: 'rank-fill', style: `width: ${introductions / top * 100}%`})),
        h('span', {class: 'rank-value'}, fmt.int(introductions))));
    });
    if (ranked.length > HUB_BARS) items.push(h('li', {class: 'legend-note'}, `Mais ${plural(ranked.length - HUB_BARS, 'base', 'bases')} com menos introduções.`));
    bars.replaceChildren(...items);
  }

  function showChain() {
    const input = toggle('chain');
    input.checked = true;
    input.dispatchEvent(new Event('change'));
  }

  const flyTo = (center) => map.flyTo({center, zoom: Math.max(map.getZoom(), 10)});
  const flyToHub = (hub) => flyTo([hub.lon, hub.lat]);

  function chainPopup(record, lngLat) {
    const popup = new maplibregl.Popup({closeButton: true, maxWidth: '280px'});
    const then = (action) => () => { popup.remove(); action(); };
    const farmLink = (id) => id
      ? h('button', {type: 'button', class: 'link', title: 'Abrir a granja e mostrá-la no mapa', onclick: then(() => selectFarm(id, {fly: true}))}, id)
      : '—';
    const local = record.pathway === 'local';
    const rows = [
      ['Dia', String(record.day)],
      ['Via', pathwayLabel(record.pathway)],
      [local ? 'Origem provável' : 'Origem', record.pathway === 'bird' ? (record.bird_id ? `ave silvestre ${record.bird_id}` : '—')
        : record.source_farm_id && record.source_farm_id === record.farm_id ? 'a própria granja (reinfecção pelo vírus que ela espalhou)'
          : farmLink(record.source_farm_id)],
      ['Destino', farmLink(record.farm_id)],
    ];
    if (record.pathway === 'fomite') {
      const hub = overlay.chain.hubs.find(item => item.id === record.hub_id);
      rows.push(
        ['Base', hub ? h('button', {type: 'button', class: 'link', title: 'Mostrar a base no mapa', onclick: then(() => flyToHub(hub))}, hubName(hub))
          : 'nenhuma: carga levada direto entre granjas'],
        ['Caminhão', record.truck_id ?? '—'],
      );
    }
    if (Number.isFinite(record.share)) rows.push([local ? 'Parcela do risco' : 'Parcela atribuída', `${fmt.num(record.share * 100, 0)}%`]);
    popup.setLngLat(lngLat).setDOMContent(h('div', {class: 'hub-popup chain-popup'},
      h('strong', {}, `Introdução na granja ${record.farm_id}`),
      local ? h('p', {}, 'Vizinho com o maior risco; outros vizinhos também contribuíram.') : null,
      h('table', {class: 'kv'}, h('tbody', {}, rows.map(([key, value]) => h('tr', {}, h('th', {scope: 'row'}, key), h('td', {}, value))))))).addTo(map);
  }

  async function refreshFlocks() {
    if (!overlayOn('flocks')) { cancelOverlay('flocks'); return; }
    const day = shownDay();
    const data = await fetchOverlay('flocks', `/api/sim/flocks?from_day=${Math.max(0, day - FLOCK_DAYS + 1)}&to_day=${day}`);
    if (!data) return;
    for (const feature of data.features) feature.properties.age = day - feature.properties.day;
    map.getSource('sim-flocks').setData(data);
    if (Array.isArray(data.arrival_profile) && data.arrival_profile.length === MONTHS.length) {
      sim.arrivalProfile = data.arrival_profile;
      renderArrivalProfile();
    }
  }

  /** Monthly arrival rate relative to the peak, with the month of the day on screen marked. */
  function renderArrivalProfile() {
    const figure = $('arrival-profile'), profile = sim.arrivalProfile, date = sim.state && dateOf(shownDay());
    figure.hidden = !profile;
    if (!profile) return;
    const current = date ? date.getUTCMonth() : -1;
    const bars = $('arrival-profile-bars');
    bars.replaceChildren(...profile.map((value, month) => h('span', {
      class: `month-bar${month === current ? ' current' : ''}`, title: `${MONTHS[month]}: ${fmt.num(value * 100, 0)}% do pico`,
    }, h('span', {class: 'month-fill', style: `--v: ${Math.max(0, Math.min(1, value))}`}), h('span', {class: 'month-name'}, MONTHS[month][0].toUpperCase()))));
    bars.setAttribute('role', 'img');
    bars.setAttribute('aria-label', 'Chegadas de bandos por mês, em relação ao pico: '
      + profile.map((value, month) => `${MONTHS[month]} ${fmt.num(value * 100, 0)}%`).join(', ')
      + (current >= 0 ? `. Mês do dia exibido: ${MONTHS[current]}.` : '.'));
  }

  function flockPopup(p, lngLat) {
    const rows = [
      ['Chegada', `dia ${p.day}${dateText(p.day) ? ` (${dateText(p.day)})` : ''}`],
      ['Aves', fmt.int(p.size)],
      ['Infecção', p.infected ? 'chegou com ave infectada' : 'sem ave infectada'],
      ['Sítio de pouso', p.site || 'nenhum sítio documentado: ponto de água sorteado'],
    ];
    new maplibregl.Popup({closeButton: true, maxWidth: '260px'}).setLngLat(lngLat).setDOMContent(h('div', {class: 'hub-popup'},
      h('strong', {}, 'Bando migratório'),
      h('table', {class: 'kv'}, h('tbody', {}, rows.map(([key, value]) => h('tr', {}, h('th', {scope: 'row'}, key), h('td', {}, value))))))).addTo(map);
  }

  // Zones depend only on the run and the day, so a refresh on the same day
  // (an intervention, a live tick that did not advance) does not refetch.
  async function refreshZones() {
    if (!overlayOn('zones')) { cancelOverlay('zones'); return; }
    const day = shownDay(), key = zonesKey();
    // Cancel too: a pending fetch for another day must not overwrite this one.
    if (overlay.zones.key === key) { cancelOverlay('zones'); return; }
    const data = await fetchOverlay('zones', `/api/sim/zones?day=${day}`);
    if (!data) return;
    map.getSource('sim-zones').setData(data);
    map.getSource('sim-zones-edge').setData(zoneEdges(data));
    const foci = new Map(), radii = {};
    for (const {properties: p} of data.features) {
      foci.set(p.focus_id, Boolean(foci.get(p.focus_id) || p.standstill));
      radii[p.ring] = p.radius_km;
    }
    overlay.zones = {key, day, foci: foci.size, standstill: [...foci.values()].filter(Boolean).length, radii};
    renderZones();
    renderProtocolKpi();
  }

  function renderZones() {
    // Drawn radii first, then the run's parameters: never the document's wording.
    const values = sim.params?.values ?? {};
    for (const ring of ZONE_RINGS) {
      const radius = overlay.zones.radii[ring.key] ?? values[ring.radius];
      document.querySelector(`#legend-zones [data-ring="${ring.key}"]`).textContent =
        Number.isFinite(radius) ? `${ring.label}, até ${fmt.num(radius, 1)} km` : ring.label;
    }
    const {day, foci, standstill} = overlay.zones;
    $('zones-note').textContent = 'Raios em linha reta a partir de cada foco. ' + (!zonesShown() ? ''
      : foci ? `Dia ${day}: ${plural(foci, 'foco ativo', 'focos ativos')}, ${fmt.int(standstill)} com trânsito interrompido.`
        : `Dia ${day}: nenhum foco ativo.`);
  }

  // ------------------------------------------------------------ farm card
  function selectFarm(id, {fly = false} = {}) {
    sim.selected = id;
    sim.detail = null;
    map.setFilter('sim-selected', ['==', ['get', 'id'], id ?? '']);
    const card = $('farm-card');
    card.hidden = !id;
    if (id) card.dataset.open = 'true'; else delete card.dataset.open;
    if (!id) { detailAbort?.abort(); return; }
    const feature = sim.farms.get(id);
    if (fly && feature) map.flyTo({center: feature.geometry.coordinates, zoom: Math.max(map.getZoom(), 10)});
    renderCard();
    loadDetail();
  }

  async function loadDetail() {
    detailAbort?.abort();
    const controller = detailAbort = new AbortController();
    const id = sim.selected;
    try {
      sim.detail = await get(`/api/sim/farms/${encodeURIComponent(id)}`, {signal: controller.signal});
      if (sim.selected === id) renderCard();
    } catch (error) {
      if (isAbort(error)) return;
      $('farm-card-body').replaceChildren(h('p', {class: 'error-text'}, `Não foi possível carregar a granja: ${error.message}`));
      status(error.message, 'error');
    }
  }

  function renderCard() {
    const id = sim.selected, detail = sim.detail;
    const feature = sim.farms.get(id)?.properties;
    const name = detail?.name ?? feature?.name;
    $('farm-card-title').textContent = name || `Granja ${id}`;
    $('farm-card-sub').textContent = [detail?.municipality ?? feature?.municipality, kindLabel(detail?.kind ?? feature?.kind), name ? id : null]
      .filter(Boolean).join(', ');
    if (!feature && !detail) { $('farm-card-body').replaceChildren(h('p', {class: 'muted'}, 'Carregando…')); return; }
    // Compartments and status follow the day on screen: the farm collection
    // (live or replayed), refetched with every refresh, so a farm restocked by
    // the last step unlocks before its slower detail request returns.
    const historyRow = detail?.history?.findLast(row => row.day === shownDay());
    const current = feature || (!replaying() && detail) || historyRow || {};
    const farmStatus = current.status ?? detail?.status;
    const culled = current.culled ?? farmStatus === 'culled';
    const production = PRODUCTIONS.find(p => p.key === (feature ?? detail).production);
    const seirTotal = SEIR.reduce((sum, c) => sum + (current[c.key] ?? 0), 0);
    const seirBar = h('div', {class: 'seg-bar tall'});
    segmentedBar(seirBar, SEIR.map(c => ({label: c.label, value: current[c.key] ?? 0, color: `seir-${c.key}`, text: fmt.int(current[c.key] ?? 0)})),
      `Compartimentos da granja no dia ${shownDay()}`);

    const body = [
      h('p', {class: 'farm-status'}, h('i', {class: `mk mk-${farmStatus}`}), statusLabel(farmStatus),
        replaying() ? h('span', {class: 'muted'}, ` no dia ${sim.viewDay}`) : null,
        current.focus ? h('span', {class: 'farm-focus'}, h('i', {class: 'mk-focus'}), 'foco confirmado') : null),
      h('div', {class: 'seir'}, seirBar, h('ul', {class: 'legend seir-legend'}, SEIR.map(c =>
        h('li', {}, h('i', {class: 'key key-box', style: `--c: var(--seir-${c.key})`}), h('b', {}, fmt.int(current[c.key] ?? 0)), ` ${c.label.toLowerCase()}`))),
      seirTotal ? null : h('p', {class: 'muted'}, 'Sem aves vivas registradas.')),
    ];
    if (!detail) { body.push(h('p', {class: 'muted'}, 'Carregando histórico…')); $('farm-card-body').replaceChildren(...body); return; }

    const chart = h('figure', {class: 'chart mini'}, h('figcaption', {}, h('h4', {}, 'Evolução na granja'),
      h('ul', {class: 'legend'}, ['exposed', 'infected', 'dead'].map(key => h('li', {}, h('i', {class: 'key key-line', style: `--c: var(--seir-${key})`}), SEIR.find(c => c.key === key).label)))));
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('role', 'img');
    const readout = h('output', {class: 'readout'});
    chart.append(svg, readout);
    timeChart(svg, readout, {
      rows: detail.history, kind: 'line', markDay: sim.viewDay, onPick: jumpTo, height: 104, label: 'Aves expostas, infectadas e mortas na granja',
      format: fmt.int, tick: fmt.compact,
      series: ['exposed', 'infected', 'dead'].map(key => ({label: SEIR.find(c => c.key === key).label.toLowerCase(), color: `seir-${key}`, value: row => row[key]})),
    });

    const first = detail.first_infection_day, detected = detail.detection_day;
    let gap = '—';
    if (first >= 0 && detected >= 0) gap = `${detected - first} dias`;
    else if (first >= 0) gap = `${sim.state.day - first} dias sem detecção`;
    const boosted = detail.under_biosec_boost;
    const facts = [
      ['Produção', production ? `${production.label} (${production.note})` : 'tipo não informado'],
      ['Aves alojadas / capacidade', housedText(current.housed ?? detail.housed, detail.pop_total)],
      ['Biosseguridade', `${fmt.num(detail.biosecurity, 0)}%${boosted ? ' (reforçada)' : ''}`],
      ['Em quarentena', detail.quarantined ? 'sim' : 'não'],
      ...(protocolOn() ? [['Zona de emergência', zoneText(current)]] : []),
      ['Primeira infecção', first >= 0 ? `dia ${first}` : 'nenhuma'],
      ['Detecção', detected >= 0 ? `dia ${detected}` : 'não detectada'],
      ['Atraso até a detecção', gap],
      ['Base mais próxima (em linha reta)', detail.nearest_hub ? `${kindLabel(detail.nearest_hub.kind)} ${detail.nearest_hub.id}` : '—'],
    ];

    const values = sim.params?.values;
    // Interventions act on the live farm, so the hint uses live compartments.
    const alive = detail.susceptible + detail.exposed + detail.infected;
    const actions = [
      ['stamping_out', 'Sacrifício sanitário',
        `Abate ${fmt.int(alive)} aves${values ? ` (≈ ${fmt.money(alive * values.cull_cost_per_bird)})` : ''} e põe em quarentena as granjas num raio de ${fmt.num(sim.state.stamping_out_radius_km, 1)} km.`],
      ['movement_block', 'Bloqueio de movimentação',
        `Suspende as visitas de caminhões${values ? `; quarentena custa ${fmt.money(values.quarantine_cost_per_day)} por dia` : ''}.`],
      ['biosecurity_boost', 'Melhoria de biosseguridade',
        values ? `Biosseguridade ${fmt.num(detail.biosecurity, 0)}% → ${fmt.num(Math.min(100, detail.biosecurity + values.biosecurity_boost_amount), 0)}% por ${fmt.money(values.biosecurity_investment_cost)}.`
          : 'Aumenta a biosseguridade da granja.'],
    ];
    const disabledWhy = replaying() ? 'Volte ao vivo para intervir: intervenções valem para o dia atual.'
      : culled ? `Granja já sacrificada${protocolOn() ? '; volta a aceitar intervenções quando for repovoada' : ''}.` : null;

    body.push(
      chart,
      h('table', {class: 'kv'}, h('tbody', {}, facts.map(([k, v]) => h('tr', {}, h('th', {scope: 'row'}, k), h('td', {}, v))))),
      h('section', {class: 'card-actions', 'aria-label': 'Intervenções nesta granja'}, h('h4', {}, 'Intervir'),
        disabledWhy ? h('p', {class: 'muted'}, disabledWhy) : null,
        ...actions.map(([action, label, hint]) => h('div', {class: 'action'},
          h('button', {type: 'button', class: action === 'stamping_out' ? 'danger' : '', disabled: Boolean(disabledWhy),
            onclick: () => intervene(action, label)}, label),
          h('p', {class: 'hint'}, hint)))),
      cardList('Eventos recentes', detail.events.slice(-CARD_ITEMS).reverse().map(e =>
        h('li', {}, h('b', {}, `Dia ${e.day}`), ` ${eventLabel(e.type)}: `, h('span', {class: 'muted'}, e.details))), 'Nenhum evento nesta granja.'),
      cardList('Visitas de caminhões', detail.visits.slice(-CARD_ITEMS).reverse().map(v =>
        h('li', {class: v.introduced_infection ? 'introduced' : ''}, h('b', {}, `Dia ${v.day}`),
          ` caminhão ${v.truck_id}, `, placeLink(v.origin_id), ' → ', placeLink(v.destination_id), `, carga ${fmt.num(v.load_on_arrival * 100, 0)}%`,
          v.introduced_infection ? h('span', {class: 'tag'}, 'introduziu infecção') : null)), 'Nenhuma visita registrada.'),
    );
    $('farm-card-body').replaceChildren(...body);
  }
  /** A farm or hub id in the card, as a button that shows it on the map. */
  function placeLink(id) {
    if (id === sim.selected) return id;
    if (sim.farms.has(id)) return h('button', {type: 'button', class: 'link inline', title: 'Abrir a granja e mostrá-la no mapa', onclick: () => selectFarm(id, {fly: true})}, id);
    const hub = sim.hubs.get(id);
    if (hub) return h('button', {type: 'button', class: 'link inline', title: 'Mostrar a base no mapa', onclick: () => flyTo(hub.geometry.coordinates)}, id);
    return id ?? '—';
  }
  /** Birds housed now against capacity; below capacity while partly restocked or empty. */
  function housedText(housed, capacity) {
    if (!Number.isFinite(housed)) return fmt.int(capacity);
    const share = capacity > 0 && housed < capacity ? ` (${fmt.num(housed / capacity * 100, 0)}%)` : '';
    return `${fmt.int(housed)} / ${fmt.int(capacity)}${share}`;
  }
  /** `zone` absent (an older snapshot) is unknown; null means outside every ring. */
  function zoneText({zone, focus}) {
    if (zone === undefined) return '—';
    if (zone === null) return 'fora das zonas';
    return focus ? `${zoneLabel(zone)}, granja foco` : zoneLabel(zone);
  }
  const cardList = (title, items, emptyText) => h('section', {}, h('h4', {}, title),
    items.length ? h('ul', {class: 'card-list'}, items) : h('p', {class: 'muted'}, emptyText));

  async function intervene(action, label) {
    const id = sim.selected;
    const ok = await simAction(() => post('/api/sim/intervene', {farm_id: id, action}));
    if (ok) status(`Intervenção aplicada na granja ${id}: ${label.toLowerCase()}.`);
    else if (ok === null) status('Aguarde o passo atual terminar para intervir.', 'error');
  }

  // ------------------------------------------------------------ scenario editor
  async function openScenario() {
    const dialog = $('scenario');
    $('scenario-error').hidden = true;
    try {
      sim.params = await get('/api/sim/params');
    } catch (error) { status(`Parâmetros: ${error.message}`, 'error'); return; }
    $('scenario-seed').value = sim.state.seed;
    const {values, defaults, schema} = sim.params;
    const groups = new Map();
    for (const field of schema) {
      if (!groups.has(field.group)) groups.set(field.group, []);
      groups.get(field.group).push(field);
    }
    $('scenario-fields').replaceChildren(...[...groups].map(([group, fields]) =>
      h('fieldset', {class: 'param-group'}, h('legend', {}, group),
        fields.some(field => field.name === 'protocol_enabled') ? h('p', {class: 'param-group-source'},
          'Fontes: ', PROTOCOL_SOURCES.flatMap((source, i) => [i ? '; ' : '', h('cite', {}, source)]),
          '. Cada parâmetro cita a sua no selo "fonte"; valores marcados como suposição não constam nos documentos.') : null,
        ...fields.map(field => paramRow(field, values[field.name], defaults[field.name])))));
    const presets = sim.params.presets ?? [];
    $('scenario-preset-row').hidden = !presets.length;
    // "Personalizado" is shown, never chosen: it marks fields that match no preset.
    $('scenario-preset').replaceChildren(...presets.map(preset => h('option', {value: preset.id}, preset.label)),
      h('option', {value: '', disabled: true}, 'Personalizado'));
    scenarioChanged();
    dialog.showModal();
  }

  const numberInputs = () => [...$('scenario-fields').querySelectorAll('input[type=number]')];
  /** A preset is the defaults with its overrides on top, not the current values. */
  const presetValues = (preset) => ({...sim.params.defaults, ...preset.values});

  function applyPreset() {
    const preset = sim.params.presets.find(item => item.id === $('scenario-preset').value);
    if (!preset) return;
    const values = presetValues(preset);
    for (const input of numberInputs()) {
      input.value = values[input.name];
      input.dispatchEvent(new Event('input'));
    }
  }

  function syncPreset() {
    const presets = sim.params?.presets ?? [];
    if (!presets.length) return;
    const match = presets.find(preset => {
      const values = presetValues(preset);
      return numberInputs().every(input => Number(input.value) === Number(values[input.name]));
    });
    $('scenario-preset').value = match?.id ?? '';
    $('scenario-preset-note').textContent = match?.description ?? 'Valores alterados à mão. Escolha um cenário para preencher todos os campos.';
  }

  function paramRow(field, value, fallback) {
    const id = `param-${field.name}`;
    const unit = field.unit ? ` ${field.unit}` : '';
    const attrs = {min: field.min, max: field.max, step: field.step ?? (field.type === 'int' ? 1 : 'any')};
    const range = h('input', {type: 'range', ...attrs, value, 'aria-label': `${field.label} (controle deslizante)`, tabindex: '-1'});
    // Where the value comes from: a badge (title on hover, a hint on focus or tap).
    const hintId = `${id}-source`;
    const badge = field.assumption || field.source ? h('span', {
      class: `source-badge${field.assumption ? ' assumption' : ''}`, tabindex: field.source ? '0' : null,
      title: field.source, 'aria-describedby': field.source ? hintId : null,
    }, field.assumption ? 'suposição' : 'fonte') : null;
    const hint = field.source ? h('span', {class: 'source-hint', id: hintId, role: 'tooltip'}, field.source) : null;
    const number = h('input', {type: 'number', id, name: field.name, ...attrs, value, required: true, 'data-type': field.type, 'data-default': fallback,
      'aria-describedby': hint ? hintId : null});
    const row = h('div', {class: 'param'},
      h('div', {class: 'param-head'}, h('label', {for: id}, field.label, h('span', {class: 'sr-only modified-text'}, ' (alterado)')), badge, hint),
      h('div', {class: 'param-inputs'}, range, number, h('span', {class: 'unit'}, field.unit ?? '')),
      h('p', {class: 'param-default'}, `padrão: ${fmt.num(fallback, 4)}${unit}`,
        h('button', {type: 'button', class: 'link', onclick: () => { number.value = fallback; range.value = fallback; mark(); }}, 'restaurar')));
    const mark = () => { row.classList.toggle('modified', Number(number.value) !== Number(fallback)); scenarioChanged(); };
    range.addEventListener('input', () => { number.value = range.value; mark(); });
    number.addEventListener('input', () => { if (number.value !== '') range.value = number.value; mark(); });
    row.classList.toggle('modified', Number(value) !== Number(fallback));
    return row;
  }
  function scenarioChanged() {
    const count = $('scenario-fields').querySelectorAll('.param.modified').length;
    $('scenario-modified').textContent = count ? `${count} parâmetro${count > 1 ? 's' : ''} diferente${count > 1 ? 's' : ''} do padrão` : 'Todos os parâmetros no padrão';
    syncPreset();
  }
  function restoreDefaults() {
    for (const input of $('scenario-fields').querySelectorAll('input[type=number]')) {
      input.value = input.dataset.default;
      input.dispatchEvent(new Event('input'));
    }
  }
  async function applyScenario(event) {
    event.preventDefault();
    const error = $('scenario-error');
    const fail = (message) => { error.textContent = message; error.hidden = false; };
    error.hidden = true;
    const seedText = $('scenario-seed').value.trim();
    const seed = Number(seedText);
    if (seedText === '' || !Number.isSafeInteger(seed) || seed < 0) return fail('A semente deve ser um inteiro maior ou igual a zero.');
    const params = {};
    for (const input of $('scenario-fields').querySelectorAll('input[type=number]')) {
      const value = Number(input.value);
      if (input.value === '' || !Number.isFinite(value)) return fail(`${input.labels[0].firstChild.textContent}: informe um número.`);
      if (input.dataset.type === 'int' && !Number.isInteger(value)) return fail(`${input.labels[0].firstChild.textContent}: precisa ser inteiro.`);
      params[input.name] = value;
    }
    if (sim.busy) return fail('Aguarde o passo atual terminar e tente de novo.');
    setRunning(false);
    sim.busy = true;
    $('scenario-apply').disabled = true;
    try {
      await post('/api/sim/reset', {seed, params});
      overlay.run += 1;
      resetEvents();
      selectFarm(null);
      await refreshLive();
      $('scenario').close();
      status(`Cenário aplicado: simulação reiniciada com a semente ${seed}.`);
      sim.params = {...sim.params, values: {...sim.params.values, ...params}};
    } catch (err) {
      fail(err instanceof ApiError && err.status === 422 ? `O servidor recusou os parâmetros: ${err.message}` : err.message);
    } finally {
      sim.busy = false;
      $('scenario-apply').disabled = false;
    }
  }

  // ------------------------------------------------------------ wiring
  function handleClick(event) {
    const shown = (ids) => ids.filter(id => map.getLayoutProperty(id, 'visibility') !== 'none');
    const query = (where, ids) => ids.length ? map.queryRenderedFeatures(where, {layers: ids}) : [];
    const hits = query(event.point, shown(['sim-farms', 'sim-hubs']));
    const farm = hits.find(hit => hit.layer.id === 'sim-farms');
    if (farm) { selectFarm(farm.properties.id); return; }
    const hub = hits.find(hit => hit.layer.id === 'sim-hubs');
    if (hub) { hubPopup(hub.properties, hub.geometry.coordinates); return; }
    const [flock] = query(event.point, shown(FLOCK_CLICK_LAYERS));
    if (flock) { flockPopup(flock.properties, flock.geometry.coordinates); return; }
    const {x, y} = event.point;
    const [link] = query([[x - CLICK_SLOP_PX, y - CLICK_SLOP_PX], [x + CLICK_SLOP_PX, y + CLICK_SLOP_PX]], shown(CHAIN_CLICK_LAYERS));
    if (link) chainPopup(link.properties, event.lngLat);
  }

  function hubPopup(p, lngLat) {
    const kv = (key, value) => h('tr', {}, h('th', {}, key), h('td', {}, value));
    const rows = h('tbody', {}, kv('Carga de fômite', `${fmt.num(p.load * 100, 0)}%`), kv('Caminhões', fmt.int(p.trucks)));
    new maplibregl.Popup({closeButton: true, maxWidth: '280px'}).setLngLat(lngLat).setDOMContent(h('div', {class: 'hub-popup'},
      h('strong', {}, p.name || `Base ${p.id}`), h('p', {}, kindLabel(p.kind)), h('table', {class: 'kv'}, rows))).addTo(map);
    // Product hubs (entrepostos, abatedouros) also list the lots they received.
    const day = shownDay();
    marketHubs().then(hubs => {
      const tally = hubs?.find(item => item.id === p.id);
      if (!tally || !rows.isConnected) return;
      const unit = PRODUCTS.find(item => item.key === tally.product)?.unit ?? '';
      rows.append(
        kv(`Lotes até o dia ${day}`, `${fmt.int(tally.lots)} (${fmt.compact(tally.quantity)} ${unit})`),
        kv('Expostos', `${fmt.int(tally.exposed_lots)}${tally.exposed_lots ? ` (${fmt.compact(tally.exposed_quantity)} ${unit})` : ''}`),
        kv('Recolhidos', fmt.int(tally.recalled_lots)),
        kv('Expostos fora da janela', fmt.int(tally.outside_window_lots)));
    });
  }

  /** Map filter for the "Mostrar granjas" choice; status and focus filters are combined with it. */
  function applyFarmType() {
    const type = sim.farmType ? ['==', ['get', 'production'], sim.farmType] : true;
    map.setFilter('sim-farms', type === true ? null : type);
    map.setFilter('sim-farms-type', ['all', ['==', ['get', 'production'], 'postura'], type]);
    map.setFilter('sim-focus', ['all', IS_FOCUS, type]);
  }

  function wire() {
    $('sim-step').onclick = () => { if (!sim.running) step(); };
    $('sim-run').onclick = toggleRun;
    $('go-live').onclick = goLive;
    $('scrub').addEventListener('input', () => {
      setRunning(false);
      const day = Number($('scrub').value);
      $('playback-day').textContent = `Dia ${day}`;
      clearTimeout(scrubTimer);
      scrubTimer = setTimeout(() => day === sim.state.snapshot_days[1] ? goLive() : loadDay(day), SCRUB_DEBOUNCE_MS);
    });
    for (const input of document.querySelectorAll('input[name=risk-mode]')) {
      input.addEventListener('change', () => { sim.riskMode = input.value; paintRisk(); });
    }
    for (const button of document.querySelectorAll('[data-chart-scope]')) {
      button.addEventListener('click', () => {
        sim.chartScope = button.dataset.chartScope;
        for (const other of document.querySelectorAll('[data-chart-scope]')) other.setAttribute('aria-pressed', String(other === button));
        renderCharts();
      });
    }
    $('data-table').addEventListener('toggle', renderTable);
    $('markets-hubs').addEventListener('toggle', renderMarketHubs);
    for (const input of document.querySelectorAll('input[name=farm-type]')) {
      input.addEventListener('change', () => { sim.farmType = input.value; applyFarmType(); });
    }
    for (const [name, refresh] of [['heat', refreshHeat], ['chain', refreshChain], ['flocks', refreshFlocks], ['zones', refreshZones]]) {
      toggle(name).addEventListener('change', () => { if (toggle(name).checked) refresh(); else clearOverlay(name); });
    }
    $('heat-weight').addEventListener('change', paintHeat);
    $('heat-window').addEventListener('change', refreshHeat);
    for (const input of document.querySelectorAll('input[name=chain-window]')) input.addEventListener('change', refreshChain);
    $('scenario-preset').addEventListener('change', applyPreset);
    $('farm-card-close').onclick = () => selectFarm(null);
    $('open-scenario').onclick = openScenario;
    $('scenario-defaults').onclick = restoreDefaults;
    $('scenario-cancel').onclick = () => $('scenario').close();
    $('scenario-form').addEventListener('submit', applyScenario);
    map.on('mouseenter', 'sim-farms', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'sim-farms', () => { map.getCanvas().style.cursor = ''; });
    for (const layer of ['sim-hubs', ...CHAIN_CLICK_LAYERS, ...FLOCK_CLICK_LAYERS]) {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    }
    document.addEventListener('keydown', event => {
      if (!isActive() || event.ctrlKey || event.metaKey || event.altKey || $('scenario').open) return;
      // Let focused controls keep their own Space/arrow behaviour.
      if (event.target.closest('input, select, textarea, button, a, summary, svg, [contenteditable]')) return;
      if (event.key === ' ') { event.preventDefault(); toggleRun(); }
      // A focused map pans with the arrow keys; leave those to MapLibre.
      else if (event.key === 'ArrowRight' && !event.target.closest('.maplibregl-map')) { event.preventDefault(); if (!sim.running) step(); }
    });
  }

  async function start() {
    wire();
    paint();
    get('/api/sim/params').then(params => { sim.params = params; renderZones(); }, error => status(`Parâmetros: ${error.message}`, 'error'));
    await simAction(async () => {});
  }

  return {
    start, paint, handleClick, pause: () => setRunning(false),
    hiddenLayers: () => new Set([...(replaying() ? SIM_GROUPS.simRisk : []), ...(protocolOn() ? [] : SIM_GROUPS.simZones)]),
    closeCard: () => selectFarm(null),
  };
}
