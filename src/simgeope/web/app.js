/* No build step. All geographic/routing calculations belong to the world API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const empty = () => ({type: 'FeatureCollection', features: []});
  const groups = {admin: ['admin-fill', 'admin-line'], roads: ['roads'], hydro: ['hydro-fill', 'hydro-line'], crossings: ['crossings'], facilities: ['facilities'], environment: ['environment'],
    simFarms: ['sim-farms', 'sim-selected'], simHubs: ['sim-hubs'], simTrucks: ['sim-trucks'], simBirds: ['sim-birds'], simRisk: ['sim-risk'], simInterventions: ['sim-interventions-fill', 'sim-interventions-line']};
  // Simulation layers exist only when the server runs with --simulate.
  const simSources = ['farms', 'hubs', 'movements', 'birds', 'risk', 'interventions'];
  const farmColors = ['match', ['get', 'status'], 'healthy', '#2e9e44', 'quarantined', '#e3c21b', 'hidden', '#ef8a17', 'notified', '#d62d20', '#222'];
  let map, viewportAbort, timer, routeAbort, queryAbort, selecting = false, endpoints = [], markers = [];
  let simRunning = false, simBusy = false, simEventIndex = 0, selectedFarm = null;
  const status = (message, error = false) => { $('status').textContent = message; $('status').classList.toggle('error', error); };
  async function get(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }
  function visibility() {
    document.querySelectorAll('[data-layer]').forEach(input => {
      for (const id of groups[input.dataset.layer]) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', input.checked ? 'visible' : 'none');
    });
  }
  async function refresh() {
    viewportAbort?.abort(); viewportAbort = new AbortController();
    const bounds = map.getBounds();
    const bbox = [bounds.getWest(), Math.max(-90, bounds.getSouth()), bounds.getEast(), Math.min(90, bounds.getNorth())];
    if (bbox[2] - bbox[0] > 12 || bbox[3] - bbox[1] > 12) {
      for (const name of ['roads', 'hydro', 'facilities', 'environment']) map.getSource(name).setData(empty());
      status('Zoom in to load viewport layers (maximum 12° span).'); return;
    }
    const enabled = [...document.querySelectorAll('[data-layer]:checked')].map(input => input.dataset.layer);
    const names = ['roads', 'hydro', 'facilities', 'environment'].filter(name => enabled.includes(name) || (name === 'hydro' && enabled.includes('crossings')));
    try {
      status('Loading viewport layers…');
      const results = await Promise.all(names.map(async name => [name, await get(`/api/world/${name}?bbox=${bbox.join(',')}`, {signal: viewportAbort.signal})]));
      const notes = [];
      for (const [name, data] of results) {
        map.getSource(name).setData(data);
        if (data.truncated) notes.push(`${name} capped: zoom in`);
        if (data.sampled) notes.push(`environment sampled every ${data.sample_stride} cells`);
      }
      status(notes.join(' · ') || 'World layers ready. Click to inspect.');
    } catch (error) {
      if (error.name !== 'AbortError') {
        for (const name of names) map.getSource(name).setData(empty());
        status(error.message, true);
      }
    }
  }
  async function route() {
    if (endpoints.length !== 2) { status('Choose both A and B on the map.', true); return; }
    routeAbort?.abort(); routeAbort = new AbortController();
    try {
      const text = $('closed').value.trim();
      const closed = text ? text.split(',').map(value => {
        if (!/^\d+$/.test(value.trim())) throw new Error('Closed edges must be comma separated integer IDs.');
        const edge = Number(value.trim());
        if (!Number.isSafeInteger(edge)) throw new Error('Edge ID is too large.');
        return edge;
      }) : [];
      const costs = $('costs').value.trim() ? JSON.parse($('costs').value) : {};
      status('Computing route…');
      const data = await get('/api/route', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({origin: endpoints[0], destination: endpoints[1], closed_edges: closed, cost_multipliers: costs}), signal: routeAbort.signal});
      map.getSource('route').setData(data);
      $('route-metrics').textContent = `${(data.properties.distance_m / 1000).toFixed(2)} km · ${(data.properties.travel_time_s / 60).toFixed(1)} min · ${data.properties.edge_ids.length} edges`;
      status('Route ready; overlay affects only this request.');
    } catch (error) {
      if (error.name !== 'AbortError') { map.getSource('route').setData(empty()); $('route-metrics').textContent = ''; status(error.message, true); }
    }
  }
  function clearRoute() {
    routeAbort?.abort(); endpoints = []; selecting = false; markers.forEach(marker => marker.remove()); markers = [];
    $('endpoints').textContent = 'No route selected.'; $('route-metrics').textContent = '';
    map.getSource('route').setData(empty());
  }
  async function post(url, payload) {
    return get(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  }
  function lineChart(svg, rows, series, readout) {
    const W = 300, H = 110, L = 28, R = 6, T = 6, B = 16;
    const node = (name, attrs, text) => {
      const element = document.createElementNS('http://www.w3.org/2000/svg', name);
      for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
      if (text !== undefined) element.textContent = text;
      return svg.appendChild(element);
    };
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.replaceChildren();
    if (!rows.length) { readout.textContent = 'Sem dados ainda: avance a simulação.'; return; }
    const first = rows[0].day, last = rows.at(-1).day;
    const top = Math.max(1, ...rows.flatMap(row => series.map(s => s.value(row))));
    const x = day => L + (last === first ? 0 : (day - first) / (last - first)) * (W - L - R);
    const y = value => T + (1 - value / top) * (H - T - B);
    node('line', {x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'axis'});
    node('text', {x: L - 4, y: y(0) + 3, 'text-anchor': 'end', class: 'tick'}, '0');
    node('text', {x: L - 4, y: y(top) + 3, 'text-anchor': 'end', class: 'tick'}, String(Math.round(top)));
    node('text', {x: L, y: H - 3, class: 'tick'}, `dia ${first}`);
    node('text', {x: W - R, y: H - 3, 'text-anchor': 'end', class: 'tick'}, `dia ${last}`);
    for (const s of series) {
      node('polyline', {points: rows.map(row => `${x(row.day)},${y(s.value(row))}`).join(' '), fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'});
    }
    const cross = node('line', {y1: T, y2: H - B, class: 'crosshair', visibility: 'hidden'});
    const hit = node('rect', {x: 0, y: 0, width: W, height: H, fill: 'transparent'});
    const show = row => readout.textContent = `dia ${row.day}: ` + series.map(s => `${s.label} ${s.value(row)}`).join(' · ');
    hit.addEventListener('mousemove', event => {
      const box = svg.getBoundingClientRect();
      const px = (event.clientX - box.left) / box.width * W;
      const row = rows.reduce((best, r) => Math.abs(x(r.day) - px) < Math.abs(x(best.day) - px) ? r : best);
      cross.setAttribute('x1', x(row.day)); cross.setAttribute('x2', x(row.day)); cross.setAttribute('visibility', 'visible');
      show(row);
    });
    hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); show(rows.at(-1)); });
    show(rows.at(-1));
  }
  function renderCharts(history) {
    lineChart($('chart-farms'), history, [
      {label: 'infectadas', color: '#2a78d6', value: row => row.hidden + row.notified},
      {label: 'em quarentena', color: '#eb6834', value: row => row.quarantined},
      {label: 'sacrificadas', color: '#1baf7a', value: row => row.culled},
    ], $('chart-farms-readout'));
    lineChart($('chart-birds'), history, [
      {label: 'infectadas', color: '#2a78d6', value: row => row.infected_birds},
    ], $('chart-birds-readout'));
  }
  function renderSimState(state) {
    const money = state.cumulative_cost.toLocaleString('pt-BR', {maximumFractionDigits: 0});
    const notes = [];
    if (state.synthetic_facilities) notes.push('granjas e bases sintéticas (mundo sem facilities)');
    if (!state.risk_available) notes.push('risco indisponível: mundo sem raster ambiental');
    $('sim-state').textContent = `Dia ${state.day} · semente ${state.seed}\n` +
      `saudáveis ${state.healthy_farms} · notificadas ${state.notified_farms} · ocultas ${state.hidden_farms}\n` +
      `Rt ${state.rt.toFixed(2)} · custo R$ ${money}\n` +
      `mortas ${Math.round(state.total_deaths)} · sacrificadas ${Math.round(state.total_culled)}\n` +
      `${state.farms} granjas · ${state.trucks} caminhões em ${state.hubs} bases\n` +
      `aves silvestres ${state.birds} (${state.infected_birds} infectadas) · chegaram ${state.birds_arrived} · mortas ${state.wild_bird_deaths}` +
      (notes.length ? `\n${notes.join(' · ')}` : '');
  }
  async function simRefresh() {
    const [state, history, ...collections] = await Promise.all([get('/api/sim/state'), get('/api/sim/history'), ...simSources.map(name => get(`/api/sim/${name}`))]);
    simSources.forEach((name, index) => map.getSource(`sim-${name}`).setData(collections[index]));
    renderSimState(state);
    renderCharts(history);
    const log = await get(`/api/sim/events?since=${simEventIndex}`);
    simEventIndex = log.next;
    const lines = log.events.map(event => `[dia ${event.day}] ${event.type}: ${event.details}`);
    if (log.skipped) lines.unshift(`… ${log.skipped} eventos omitidos`);
    const kept = ($('sim-log').textContent ? $('sim-log').textContent.split('\n') : []).concat(lines).slice(-300);
    $('sim-log').textContent = kept.join('\n');
    $('sim-log').scrollTop = $('sim-log').scrollHeight;
    if (collections[simSources.indexOf('risk')].truncated) status('Risco: muitas células, exibição limitada.');
  }
  async function simAction(action) {
    if (simBusy) return;
    simBusy = true;
    try { await action(); await simRefresh(); }
    catch (error) { simRunning = false; $('sim-run').textContent = 'Rodar'; status(error.message, true); }
    finally { simBusy = false; }
  }
  async function simRunLoop() {
    while (simRunning) {
      await simAction(() => post('/api/sim/step', {days: 1}));
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }
  function selectFarm(id) {
    selectedFarm = id;
    map.setFilter('sim-selected', ['==', ['get', 'id'], id ?? '']);
    $('sim-selected').textContent = id ? `Granja selecionada: ${id}` : 'Clique numa granja para selecioná-la.';
    $('sim-intervene').disabled = !id;
  }
  function startSimulation() {
    $('sim').hidden = false;
    // Static facility markers would hide the farm status colors.
    document.querySelector('[data-layer=facilities]').checked = false;
    for (const name of simSources) map.addSource(`sim-${name}`, {type: 'geojson', data: empty()});
    map.addLayer({id: 'sim-risk', type: 'fill', source: 'sim-risk', paint: {'fill-opacity': .45, 'fill-color': ['match', ['get', 'level'], 'red', '#d62d20', 'yellow', '#e3c21b', '#2e9e44']}}, 'admin-fill');
    map.addLayer({id: 'sim-interventions-fill', type: 'fill', source: 'sim-interventions', paint: {'fill-color': '#222', 'fill-opacity': .08}});
    map.addLayer({id: 'sim-interventions-line', type: 'line', source: 'sim-interventions', paint: {'line-color': '#222', 'line-width': 2, 'line-dasharray': [3, 2]}});
    map.addLayer({id: 'sim-trucks', type: 'line', source: 'sim-movements', paint: {'line-color': ['interpolate', ['linear'], ['get', 'load_on_arrival'], 0, '#6d7f87', 1, '#d62d20'], 'line-width': ['case', ['get', 'introduced_infection'], 6, 3], 'line-opacity': .85}});
    map.addLayer({id: 'sim-birds', type: 'circle', source: 'sim-birds', paint: {'circle-color': ['match', ['get', 'state'], 'I', '#e7229f', 'R', '#8a9a90', '#5b8fb0'], 'circle-radius': 3.5}});
    map.addLayer({id: 'sim-farms', type: 'circle', source: 'sim-farms', paint: {'circle-color': farmColors, 'circle-radius': 7, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5}});
    // Hubs sit inside the farm clusters, so they are drawn above the farms.
    map.addLayer({id: 'sim-hubs', type: 'circle', source: 'sim-hubs', paint: {'circle-color': '#7b48b5', 'circle-radius': 9, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2}});
    map.addLayer({id: 'sim-selected', type: 'circle', source: 'sim-farms', filter: ['==', ['get', 'id'], ''], paint: {'circle-color': 'rgba(0,0,0,0)', 'circle-radius': 12, 'circle-stroke-color': '#16667c', 'circle-stroke-width': 3}});
    $('sim-step').onclick = () => simAction(() => post('/api/sim/step', {days: 1}));
    $('sim-run').onclick = () => {
      simRunning = !simRunning;
      $('sim-run').textContent = simRunning ? 'Pausar' : 'Rodar';
      if (simRunning) simRunLoop();
    };
    $('sim-reset').onclick = () => simAction(async () => {
      const seed = $('sim-seed').value.trim();
      await post('/api/sim/reset', seed ? {seed: Number(seed)} : {});
      simEventIndex = 0; $('sim-log').textContent = ''; selectFarm(null);
    });
    $('sim-intervene').onclick = () => simAction(() => post('/api/sim/intervene', {farm_id: selectedFarm, action: $('sim-action').value}));
    return simAction(async () => {
      const state = await get('/api/sim/state');
      $('sim-seed').value = state.seed;
    });
  }
  async function start() {
    if (!window.maplibregl) { status('MapLibre did not load. Browser internet is required for the CDN.', true); return; }
    const metadata = await get('/api/world/metadata');
    $('metadata').textContent = JSON.stringify(metadata, null, 2);
    map = new maplibregl.Map({container: 'map', center: [-37, -8], zoom: 6, renderWorldCopies: false, style: {
      version: 8, sources: {osm: {type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors'}}, layers: [{id: 'osm', type: 'raster', source: 'osm'}]
    }});
    map.addControl(new maplibregl.NavigationControl());
    map.fitBounds([[metadata.bounds[0], metadata.bounds[1]], [metadata.bounds[2], metadata.bounds[3]]], {padding: 35, duration: 0});
    map.on('load', async () => {
      for (const name of ['admin', 'roads', 'hydro', 'facilities', 'environment', 'route']) map.addSource(name, {type: 'geojson', data: empty()});
      map.addLayer({id: 'environment', type: 'fill', source: 'environment', paint: {'fill-opacity': .55, 'fill-color': ['case', ['==', ['get', 'water_distance_m'], null], '#aab0b3', ['interpolate', ['linear'], ['get', 'water_distance_m'], 0, '#156c9d', 1000, '#52b4c4', 5000, '#c9dc97', 20000, '#e5c477']]}});
      map.addLayer({id: 'admin-fill', type: 'fill', source: 'admin', paint: {'fill-color': '#8bb5a8', 'fill-opacity': .08}});
      map.addLayer({id: 'admin-line', type: 'line', source: 'admin', paint: {'line-color': '#466566', 'line-width': 1.3}});
      map.addLayer({id: 'hydro-fill', type: 'fill', source: 'hydro', filter: ['==', ['geometry-type'], 'Polygon'], paint: {'fill-color': '#238cb9', 'fill-opacity': .45}});
      map.addLayer({id: 'hydro-line', type: 'line', source: 'hydro', filter: ['==', ['geometry-type'], 'LineString'], paint: {'line-color': '#238cb9', 'line-width': 2}});
      map.addLayer({id: 'roads', type: 'line', source: 'roads', paint: {'line-color': '#a96430', 'line-width': 2}});
      map.addLayer({id: 'crossings', type: 'circle', source: 'hydro', filter: ['==', ['get', 'layer'], 'crossings'], paint: {'circle-color': '#7b48b5', 'circle-radius': 4, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1}});
      map.addLayer({id: 'facilities', type: 'circle', source: 'facilities', paint: {'circle-color': '#d53a48', 'circle-radius': 6, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1}});
      map.addLayer({id: 'route-line', type: 'line', source: 'route', paint: {'line-color': '#e7229f', 'line-width': 5}});
      map.addLayer({id: 'route-point', type: 'circle', source: 'route', filter: ['==', ['geometry-type'], 'Point'], paint: {'circle-color': '#e7229f', 'circle-radius': 7}});
      if (metadata.simulation) await startSimulation();
      visibility();
      try { map.getSource('admin').setData(await get('/api/world/municipalities')); } catch (error) { status(error.message, true); }
      await refresh();
      map.on('moveend', () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
      document.querySelectorAll('[data-layer]').forEach(input => input.addEventListener('change', () => { visibility(); refresh(); }));
      $('choose-route').onclick = () => { clearRoute(); selecting = true; status('Click A, then B. Facility markers use the facility coordinate.'); };
      $('clear-route').onclick = clearRoute;
      $('requery').onclick = route;
      map.on('click', async event => {
        const hits = map.queryRenderedFeatures(event.point, {layers: Object.values(groups).flat().filter(id => map.getLayer(id))});
        const coordinate = [event.lngLat.lng, event.lngLat.lat];
        if (selecting) {
          const facility = hits.find(hit => hit.layer.id === 'facilities');
          const location = facility ? [Number(facility.properties.lon), Number(facility.properties.lat)] : coordinate;
          endpoints.push(location); markers.push(new maplibregl.Marker({color: endpoints.length === 1 ? '#136b64' : '#e7229f'}).setLngLat(location).addTo(map));
          $('endpoints').textContent = endpoints.map((p, index) => `${index ? 'B' : 'A'}: ${p.map(x => x.toFixed(5)).join(', ')}`).join(' · ');
          if (endpoints.length === 2) { selecting = false; await route(); }
          return;
        }
        const farm = hits.find(hit => hit.layer.id === 'sim-farms');
        if (farm) selectFarm(farm.properties.id);
        queryAbort?.abort(); queryAbort = new AbortController();
        const selected = hits.map(hit => ({layer: hit.layer.id, ...hit.properties}));
        $('inspection').textContent = JSON.stringify({features: selected, coordinate}, null, 2);
        try {
          const query = await get(`/api/query/point?lon=${coordinate[0]}&lat=${coordinate[1]}`, {signal: queryAbort.signal});
          $('inspection').textContent = JSON.stringify({features: selected, query}, null, 2);
        } catch (error) { if (error.name !== 'AbortError') status(error.message, true); }
      });
    });
  }
  start().catch(error => status(error.message, true));
})();
