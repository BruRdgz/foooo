/* No build step. All geographic/routing calculations belong to the world API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const empty = () => ({type: 'FeatureCollection', features: []});
  const groups = {admin: ['admin-fill', 'admin-line'], roads: ['roads'], hydro: ['hydro-fill', 'hydro-line'], crossings: ['crossings'], facilities: ['facilities'], environment: ['environment']};
  let map, viewportAbort, timer, routeAbort, queryAbort, selecting = false, endpoints = [], markers = [];
  const status = (message, error = false) => { $('status').textContent = message; $('status').classList.toggle('error', error); };
  async function get(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }
  function visibility() {
    document.querySelectorAll('[data-layer]').forEach(input => {
      for (const id of groups[input.dataset.layer]) map.setLayoutProperty(id, 'visibility', input.checked ? 'visible' : 'none');
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
      visibility();
      try { map.getSource('admin').setData(await get('/api/world/municipalities')); } catch (error) { status(error.message, true); }
      await refresh();
      map.on('moveend', () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
      document.querySelectorAll('[data-layer]').forEach(input => input.addEventListener('change', () => { visibility(); refresh(); }));
      $('choose-route').onclick = () => { clearRoute(); selecting = true; status('Click A, then B. Facility markers use the facility coordinate.'); };
      $('clear-route').onclick = clearRoute;
      $('requery').onclick = route;
      map.on('click', async event => {
        const hits = map.queryRenderedFeatures(event.point, {layers: Object.values(groups).flat()});
        const coordinate = [event.lngLat.lng, event.lngLat.lat];
        if (selecting) {
          const facility = hits.find(hit => hit.layer.id === 'facilities');
          const location = facility ? [Number(facility.properties.lon), Number(facility.properties.lat)] : coordinate;
          endpoints.push(location); markers.push(new maplibregl.Marker({color: endpoints.length === 1 ? '#136b64' : '#e7229f'}).setLngLat(location).addTo(map));
          $('endpoints').textContent = endpoints.map((p, index) => `${index ? 'B' : 'A'}: ${p.map(x => x.toFixed(5)).join(', ')}`).join(' · ');
          if (endpoints.length === 2) { selecting = false; await route(); }
          return;
        }
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
