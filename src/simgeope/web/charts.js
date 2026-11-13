// Hand-rolled SVG time charts (stacked area, line, stacked bars) with a shared
// crosshair + readout, plus the segmented "part of a whole" bar.
import {h} from './common.js';

const SVG = 'http://www.w3.org/2000/svg';
const W = 340, L = 38, R = 10, T = 10, B = 18;

function svgNode(parent, name, attrs = {}, text) {
  const element = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  parent.appendChild(element);
  return element;
}
const paint = (element, property, colorToken) => { element.style[property] = `var(--${colorToken})`; return element; };

/** Smallest "nice" number (1, 2, 2.5, 5 × 10^k) that is >= value. */
function niceMax(value) {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map(m => m * power).find(m => m >= value * (1 - 1e-9));
}

/**
 * Render one time chart into `svg` and keep `readout` in sync with the hovered day.
 * spec: {rows, kind: 'stack'|'line'|'bars', series: [{key, label, color, value(row), faint?}],
 *        format(value), tick(value), refLine?: {value, label}, markDay?, onPick?(day), height?, label}
 * Rows are one per consecutive simulated day. A `faint` line series is drawn
 * thin and translucent, as context behind an emphasised one.
 */
export function timeChart(svg, readout, spec) {
  const {rows, kind, series, format, refLine, markDay, onPick} = spec;
  const tick = spec.tick ?? format;
  const H = spec.height ?? 132;
  const plotW = W - L - R, plotH = H - T - B;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.replaceChildren();
  readout.replaceChildren();
  if (!rows.length) {
    readout.append(h('span', {class: 'readout-empty'}, 'Sem dados ainda: avance a simulação.'));
    svg.setAttribute('aria-label', `${spec.label}: sem dados.`);
    return;
  }
  const n = rows.length;
  const stacked = kind === 'stack' || kind === 'bars';
  const totals = rows.map(row => stacked
    ? series.reduce((sum, s) => sum + Math.max(0, s.value(row)), 0)
    : Math.max(...series.map(s => s.value(row)).filter(Number.isFinite), 0));
  const top = niceMax(Math.max(...totals, refLine ? refLine.value * 1.2 : 0));
  const band = plotW / n;
  const x = kind === 'bars'
    ? (i) => L + (i + 0.5) * band
    : (i) => L + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (value) => T + (1 - Math.min(value, top) / top) * plotH;

  // Recessive frame: hairline grid at 0, ½, max; first/last day under the axis.
  const labelled = new Set();
  for (const value of [0, top, top / 2]) {
    svgNode(svg, 'line', {x1: L, x2: W - R, y1: y(value), y2: y(value), class: value === 0 ? 'axis' : 'grid'});
    // An integer formatter can round ½·max onto 0 or max; label each text once.
    if (labelled.has(tick(value))) continue;
    labelled.add(tick(value));
    svgNode(svg, 'text', {x: L - 5, y: y(value) + 3, 'text-anchor': 'end', class: 'tick'}, tick(value));
  }
  svgNode(svg, 'text', {x: L, y: H - 4, class: 'tick'}, `dia ${rows[0].day}`);
  svgNode(svg, 'text', {x: W - R, y: H - 4, 'text-anchor': 'end', class: 'tick'}, `dia ${rows.at(-1).day}`);

  if (kind === 'stack') {
    let lower = rows.map(() => 0);
    for (const s of series) {
      const upper = rows.map((row, i) => lower[i] + Math.max(0, s.value(row)));
      const edge = rows.map((_, i) => `${x(i)},${y(upper[i])}`);
      const base = rows.map((_, i) => `${x(i)},${y(lower[i])}`).reverse();
      // 1px surface seam between bands: a 2px gap would erase one-farm-thin bands.
      paint(svgNode(svg, 'polygon', {points: edge.concat(base).join(' '), class: 'band'}), 'fill', s.color);
      lower = upper;
    }
  } else if (kind === 'bars') {
    const gap = band >= 5 ? 2 : 0;
    const width = Math.max(0.6, Math.min(24, band - gap));
    rows.forEach((row, i) => {
      let base = 0;
      const parts = series.map(s => ({s, value: Math.max(0, s.value(row))})).filter(part => part.value > 0);
      parts.forEach((part, j) => {
        const y0 = y(base), y1 = y(base + part.value);
        base += part.value;
        const height = Math.max(0, y0 - y1 - (j < parts.length - 1 ? gap / 2 : 0));
        const left = x(i) - width / 2;
        const radius = j === parts.length - 1 && width >= 6 ? Math.min(4, width / 2, height) : 0;
        const d = radius
          ? `M${left},${y0}V${y0 - height + radius}Q${left},${y0 - height} ${left + radius},${y0 - height}` +
            `H${left + width - radius}Q${left + width},${y0 - height} ${left + width},${y0 - height + radius}V${y0}Z`
          : `M${left},${y0}V${y0 - height}H${left + width}V${y0}Z`;
        paint(svgNode(svg, 'path', {d, class: 'bar'}), 'fill', part.s.color);
      });
    });
  } else {
    if (refLine) {
      svgNode(svg, 'line', {x1: L, x2: W - R, y1: y(refLine.value), y2: y(refLine.value), class: 'ref'});
      svgNode(svg, 'text', {x: W - R - 2, y: y(refLine.value) - 4, 'text-anchor': 'end', class: 'tick ref-label'}, refLine.label);
    }
    for (const s of series) {
      const points = rows.map((row, i) => [x(i), s.value(row)]).filter(([, v]) => Number.isFinite(v));
      paint(svgNode(svg, 'polyline', {points: points.map(([px, v]) => `${px},${y(v)}`).join(' '), class: s.faint ? 'line faint' : 'line'}), 'stroke', s.color);
      const last = points.at(-1);
      if (last && !s.faint) paint(svgNode(svg, 'circle', {cx: last[0], cy: y(last[1]), r: 4, class: 'end-dot'}), 'fill', s.color);
    }
  }

  const markIndex = markDay === null || markDay === undefined ? -1 : rows.findIndex(row => row.day === markDay);
  if (markIndex >= 0) {
    svgNode(svg, 'line', {x1: x(markIndex), x2: x(markIndex), y1: T - 4, y2: H - B, class: 'mark'});
  }
  const cross = svgNode(svg, 'line', {y1: T, y2: H - B, class: 'crosshair', visibility: 'hidden'});
  const hit = svgNode(svg, 'rect', {x: L, y: 0, width: plotW, height: H, class: 'hit'});

  const restIndex = markIndex >= 0 ? markIndex : n - 1;
  let hover = restIndex;
  const show = (i, visible) => {
    hover = i;
    const row = rows[i];
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('visibility', visible ? 'visible' : 'hidden');
    readout.replaceChildren(
      h('span', {class: 'readout-day'}, `Dia ${row.day}`),
      ...series.map(s => h('span', {class: 'readout-item'},
        h('i', {class: `key key-${kind === 'line' ? 'line' : 'box'}${s.faint ? ' faint' : ''}`, style: `--c: var(--${s.color})`}),
        h('b', {}, format(s.value(row))), ' ', s.label)),
    );
  };
  const indexAt = (event) => {
    const box = svg.getBoundingClientRect();
    const px = (event.clientX - box.left) / box.width * W - L;
    const i = kind === 'bars' ? Math.floor(px / band) : Math.round((px / plotW) * (n - 1));
    return Math.max(0, Math.min(n - 1, i));
  };
  hit.addEventListener('pointermove', event => show(indexAt(event), true));
  hit.addEventListener('pointerleave', () => show(restIndex, false));
  if (onPick) {
    svg.classList.add('pickable');
    hit.addEventListener('click', event => onPick(rows[indexAt(event)].day));
  }
  svg.setAttribute('tabindex', '0');
  svg.onkeydown = (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); event.stopPropagation();
      show(Math.max(0, Math.min(n - 1, hover + (event.key === 'ArrowRight' ? 1 : -1))), true);
    } else if ((event.key === 'Enter' || event.key === ' ') && onPick) {
      event.preventDefault(); event.stopPropagation();
      onPick(rows[hover].day);
    }
  };
  svg.onblur = () => show(restIndex, false);
  show(restIndex, false);
  const lastRow = rows[restIndex];
  svg.setAttribute('aria-label', `${spec.label}, dias ${rows[0].day} a ${rows.at(-1).day}. Dia ${lastRow.day}: ` +
    series.map(s => `${s.label} ${format(s.value(lastRow))}`).join(', ') +
    '. Use as setas para percorrer os dias' + (onPick ? ' e Enter para rever o dia no mapa.' : '.'));
}

/** Thin or tall "one whole, split into parts" bar; parts at zero draw nothing. */
export function segmentedBar(container, parts, label) {
  const total = parts.reduce((sum, part) => sum + Math.max(0, part.value), 0);
  container.replaceChildren(...parts.filter(part => part.value > 0).map(part => h('span', {
    class: 'segment',
    style: `flex-grow: ${part.value}; --c: var(--${part.color})`,
    title: `${part.label}: ${part.text ?? part.value} (${total ? Math.round(part.value / total * 100) : 0}%)`,
  })));
  container.setAttribute('role', 'img');
  container.setAttribute('aria-label', `${label}: ` + parts.map(part => `${part.label} ${part.text ?? part.value}`).join(', '));
}
