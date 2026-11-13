// "Inspeção do mundo": static world layers, viewport loading, route debug and
// point/feature inspection. All geographic computation stays in the world API.
import {$, h, empty, get, post, isAbort, status, token, fmt, kindLabel} from './common.js';

const VIEWPORT_LAYERS = ['roads', 'hydro', 'facilities', 'environment'];
const MAX_SPAN_DEGREES = 12;  // mirrors the API's bbox_span_degrees limit

export const WORLD_GROUPS = {
  admin: ['admin-fill', 'admin-line'],
  roads: ['roads'],
  hydro: ['hydro-fill', 'hydro-line'],
  crossings: ['crossings'],
  facilities: ['facilities'],
  environment: ['environment'],
  route: ['route-line', 'route-point'],
};

const LAYER_LABELS = {
  'admin-fill': 'Município', 'admin-line': 'Município', roads: 'Estrada', 'hydro-fill': 'Corpo d’água',
  'hydro-line': 'Curso d’água', crossings: 'Cruzamento estrada/água', facilities: 'Instalação',
  environment: 'Célula ambiental', 'route-line': 'Rota', 'route-point': 'Ponto da rota',
};

export function addWorldLayers(map) {
  for (const name of ['admin', ...VIEWPORT_LAYERS, 'route']) map.addSource(name, {type: 'geojson', data: empty()});
  map.addLayer({id: 'environment', type: 'fill', source: 'environment', paint: {'fill-opacity': .55}});
  map.addLayer({id: 'admin-fill', type: 'fill', source: 'admin', paint: {'fill-opacity': .04}});
  map.addLayer({id: 'admin-line', type: 'line', source: 'admin', paint: {'line-width': .8}});
  map.addLayer({id: 'hydro-fill', type: 'fill', source: 'hydro', filter: ['==', ['geometry-type'], 'Polygon'], paint: {'fill-opacity': .4}});
  map.addLayer({id: 'hydro-line', type: 'line', source: 'hydro', filter: ['==', ['geometry-type'], 'LineString'], paint: {'line-width': 1.6}});
  map.addLayer({id: 'roads', type: 'line', source: 'roads', paint: {'line-width': 1.6}});
  map.addLayer({id: 'crossings', type: 'circle', source: 'hydro', filter: ['==', ['get', 'layer'], 'crossings'], paint: {'circle-radius': 4, 'circle-stroke-width': 1.5}});
  map.addLayer({id: 'facilities', type: 'circle', source: 'facilities', paint: {'circle-radius': 5, 'circle-stroke-width': 1.5}});
  map.addLayer({id: 'route-line', type: 'line', source: 'route', paint: {'line-width': 5, 'line-opacity': .9}});
  map.addLayer({id: 'route-point', type: 'circle', source: 'route', filter: ['==', ['geometry-type'], 'Point'], paint: {'circle-radius': 7}});
}

/** Re-read colour tokens (startup and on light/dark change). */
export function paintWorld(map) {
  const set = (layer, property, value) => map.setPaintProperty(layer, property, value);
  set('environment', 'fill-color', ['case', ['==', ['get', 'water_distance_m'], null], token('env-none'),
    ['interpolate', ['linear'], ['get', 'water_distance_m'], 0, token('env-0'), 1000, token('env-1'), 5000, token('env-2'), 20000, token('env-3')]]);
  set('admin-fill', 'fill-color', token('world-admin'));
  set('admin-line', 'line-color', token('world-admin'));
  set('hydro-fill', 'fill-color', token('world-hydro'));
  set('hydro-line', 'line-color', token('world-hydro'));
  set('roads', 'line-color', token('world-road'));
  set('crossings', 'circle-color', token('world-crossing'));
  set('crossings', 'circle-stroke-color', token('map-ring'));
  set('facilities', 'circle-color', token('world-facility'));
  set('facilities', 'circle-stroke-color', token('map-ring'));
  set('route-line', 'line-color', token('route'));
  set('route-point', 'circle-color', token('route'));
}

// ---------------------------------------------------------------- readable tables

function cell(value) {
  if (value === null || value === undefined || value === '') return h('span', {class: 'muted'}, '—');
  if (typeof value === 'boolean') return value ? 'sim' : 'não';
  if (typeof value === 'number') return fmt.num(value, Math.abs(value) < 1000 ? 5 : 1);
  if (Array.isArray(value)) {
    if (value.every(item => typeof item !== 'object' || item === null)) return value.map(item => typeof item === 'number' ? fmt.num(item, 5) : String(item)).join(', ');
    return h('details', {}, h('summary', {}, `${value.length} itens`), ...value.slice(0, 20).map(item => kvTable(item)),
      value.length > 20 ? h('p', {class: 'muted'}, `… e mais ${value.length - 20}`) : null);
  }
  if (typeof value === 'object') return kvTable(value);
  return String(value);
}

export function kvTable(object) {
  if (object === null || typeof object !== 'object') return h('p', {}, cell(object));
  return h('table', {class: 'kv'}, h('tbody', {}, Object.entries(object).map(([key, value]) =>
    h('tr', {}, h('th', {scope: 'row'}, key), h('td', {}, cell(value))))));
}

const section = (title, ...body) => h('section', {class: 'inspect-block'}, h('h3', {}, title), ...body);

export function renderMetadata(metadata) {
  const layers = h('ul', {class: 'chips static'}, Object.entries(metadata.layers ?? {}).map(([name, present]) =>
    h('li', {class: present ? 'on' : 'off'}, `${name}: ${present ? 'disponível' : 'ausente'}`)));
  $('metadata').replaceChildren(
    kvTable({
      'Limites (WGS84)': metadata.bounds,
      'CRS': metadata.crs,
      'CRS métrico': metadata.metric_crs,
      'Simulação ativa': metadata.simulation,
      'Construído em': metadata.build_timestamp,
      'Versão do formato': metadata.world_format_version,
    }),
    section('Camadas', layers),
    section('Contagem de feições', kvTable(metadata.feature_counts ?? {})),
    section('Limites da API', kvTable(metadata.limits ?? {})),
    metadata.warnings?.length ? section(`Avisos (${metadata.warnings.length})`, h('ul', {class: 'plain'}, metadata.warnings.map(w => h('li', {}, w)))) : null,
    metadata.sources?.length ? section(`Fontes (${metadata.sources.length})`, cell(metadata.sources)) : null,
    section('Limitações', h('ul', {class: 'plain'}, (metadata.limitations ?? []).map(item => h('li', {}, item)))),
    h('details', {class: 'raw'}, h('summary', {}, 'JSON bruto'), h('pre', {}, JSON.stringify(metadata, null, 2))),
  );
}

function renderInspection(features, coordinate, query, error = null) {
  const featureBlocks = features.length
    ? features.map(feature => section(LAYER_LABELS[feature.layer] ?? feature.layer, kvTable(feature.properties)))
    : [h('p', {class: 'muted'}, 'Nenhuma feição desenhada neste ponto.')];
  const queryBlocks = query ? [
    section('Município', query.municipality ? kvTable(query.municipality) : h('p', {class: 'muted'}, 'Fora dos municípios.')),
    section('Estrada mais próxima', query.nearest_road ? kvTable(query.nearest_road) : h('p', {class: 'muted'}, 'Nenhuma estrada próxima.')),
    section('Hidrografia', query.hydro ? kvTable(query.hydro) : h('p', {class: 'muted'}, 'Mundo sem camada hidrográfica.')),
    section('Ambiente', query.environment ? kvTable(query.environment) : h('p', {class: 'muted'}, 'Sem raster ambiental neste ponto.')),
    section(`Instalações num raio de 10 km (${query.facilities.length})`, query.facilities.length
      ? h('table', {class: 'grid-table'}, h('thead', {}, h('tr', {}, h('th', {}, 'ID'), h('th', {}, 'Tipo'), h('th', {}, 'Nome'))),
        h('tbody', {}, query.facilities.slice(0, 30).map(f => h('tr', {}, h('td', {}, String(f.id ?? '—')), h('td', {}, kindLabel(f.kind)), h('td', {}, f.name ?? '—')))))
      : h('p', {class: 'muted'}, 'Nenhuma.')),
  ] : [h('p', {class: error ? 'error-text' : 'muted'}, error ?? 'Consultando o ponto…')];
  $('inspection').replaceChildren(
    h('p', {class: 'coord'}, `Ponto ${coordinate.map(v => v.toFixed(5)).join(', ')}`),
    h('h3', {class: 'group-title'}, 'Feições clicadas'), ...featureBlocks,
    h('h3', {class: 'group-title'}, 'Consulta do ponto'), ...queryBlocks,
    h('details', {class: 'raw'}, h('summary', {}, 'JSON bruto'), h('pre', {}, JSON.stringify({features, query}, null, 2))),
  );
}

// ---------------------------------------------------------------- behaviour

export function createWorld(map, {enabledLayers}) {
  let viewportAbort, routeAbort, queryAbort;
  let selecting = false, endpoints = [], markers = [];

  async function refresh() {
    viewportAbort?.abort();
    viewportAbort = new AbortController();
    const bounds = map.getBounds();
    const bbox = [bounds.getWest(), Math.max(-90, bounds.getSouth()), bounds.getEast(), Math.min(90, bounds.getNorth())];
    if (bbox[2] - bbox[0] > MAX_SPAN_DEGREES || bbox[3] - bbox[1] > MAX_SPAN_DEGREES) {
      for (const name of VIEWPORT_LAYERS) map.getSource(name).setData(empty());
      status(`Aproxime o mapa para carregar estradas, hidrografia e instalações (extensão máxima de ${MAX_SPAN_DEGREES}°).`);
      return;
    }
    const enabled = enabledLayers();
    // Crossings are drawn from the hydro source, so they need it loaded too.
    const names = VIEWPORT_LAYERS.filter(name => enabled.has(name) || (name === 'hydro' && enabled.has('crossings')));
    if (!names.length) { status('Mundo pronto. Clique no mapa para inspecionar.'); return; }
    try {
      status('Carregando camadas da área visível…', 'busy');
      const signal = viewportAbort.signal;
      const results = await Promise.all(names.map(async name => [name, await get(`/api/world/${name}?bbox=${bbox.join(',')}`, {signal})]));
      const notes = [];
      for (const [name, data] of results) {
        map.getSource(name).setData(data);
        if (data.truncated) notes.push(`${name} limitado: aproxime o mapa`);
        if (data.sampled) notes.push(`ambiente amostrado a cada ${data.sample_stride} células`);
      }
      status(notes.join('; ') || 'Camadas carregadas. Clique no mapa para inspecionar.');
    } catch (error) {
      if (isAbort(error)) return;
      for (const name of names) map.getSource(name).setData(empty());
      status(error.message, 'error');
    }
  }

  function parseOverlay() {
    const text = $('closed').value.trim();
    const closed = text ? text.split(',').map(value => {
      if (!/^\d+$/.test(value.trim())) throw new Error('Arestas fechadas: use IDs inteiros separados por vírgula.');
      const edge = Number(value.trim());
      if (!Number.isSafeInteger(edge)) throw new Error('ID de aresta grande demais.');
      return edge;
    }) : [];
    let costs = {};
    if ($('costs').value.trim()) {
      try { costs = JSON.parse($('costs').value); }
      catch { throw new Error('Multiplicadores de custo: JSON inválido (ex.: {"123": 2}).'); }
    }
    return {closed, costs};
  }

  async function route() {
    if (endpoints.length !== 2) { status('Escolha os pontos A e B no mapa.', 'error'); return; }
    routeAbort?.abort();
    routeAbort = new AbortController();
    try {
      const {closed, costs} = parseOverlay();
      status('Calculando rota…', 'busy');
      const data = await post('/api/route', {origin: endpoints[0], destination: endpoints[1], closed_edges: closed, cost_multipliers: costs}, {signal: routeAbort.signal});
      map.getSource('route').setData(data);
      const p = data.properties;
      $('route-metrics').replaceChildren(kvTable({
        'Distância': `${fmt.num(p.distance_m / 1000, 2)} km`,
        'Tempo de viagem': `${fmt.num(p.travel_time_s / 60, 1)} min`,
        'Arestas': p.edge_ids.length,
      }));
      status('Rota pronta; fechamentos e custos valem só para esta consulta.');
    } catch (error) {
      if (isAbort(error)) return;
      map.getSource('route').setData(empty());
      $('route-metrics').replaceChildren();
      status(error.message, 'error');
    }
  }

  function clearRoute() {
    routeAbort?.abort();
    endpoints = []; selecting = false;
    markers.forEach(marker => marker.remove()); markers = [];
    $('endpoints').textContent = 'Nenhuma rota selecionada.';
    $('route-metrics').replaceChildren();
    $('choose-route').setAttribute('aria-pressed', 'false');
    map.getSource('route').setData(empty());
  }

  async function handleClick(event, hits) {
    const coordinate = [event.lngLat.lng, event.lngLat.lat];
    if (selecting) {
      const facility = hits.find(hit => hit.layer.id === 'facilities');
      const location = facility ? [Number(facility.properties.lon), Number(facility.properties.lat)] : coordinate;
      endpoints.push(location);
      markers.push(new maplibregl.Marker({color: token(endpoints.length === 1 ? 'route-a' : 'route')}).setLngLat(location).addTo(map));
      $('endpoints').textContent = endpoints.map((p, i) => `${i ? 'B' : 'A'}: ${p.map(v => v.toFixed(5)).join(', ')}`).join('  /  ');
      if (endpoints.length === 2) {
        selecting = false;
        $('choose-route').setAttribute('aria-pressed', 'false');
        await route();
      } else status('Agora clique no ponto B.');
      return;
    }
    queryAbort?.abort();
    queryAbort = new AbortController();
    const features = hits.map(hit => ({layer: hit.layer.id, properties: hit.properties}));
    renderInspection(features, coordinate, null);
    try {
      const query = await get(`/api/query/point?lon=${coordinate[0]}&lat=${coordinate[1]}`, {signal: queryAbort.signal});
      renderInspection(features, coordinate, query);
    } catch (error) {
      if (isAbort(error)) return;
      renderInspection(features, coordinate, null, error.message);
      status(error.message, 'error');
    }
  }

  $('choose-route').onclick = () => {
    clearRoute();
    selecting = true;
    $('choose-route').setAttribute('aria-pressed', 'true');
    status('Clique no ponto A e depois no B. Instalações usam a própria coordenada.');
  };
  $('clear-route').onclick = clearRoute;
  $('requery').onclick = route;

  return {refresh, handleClick, cancel: () => viewportAbort?.abort()};
}
