// Shared helpers, vocabulary and the colour tokens bridge. No build step.

export const $ = (id) => document.getElementById(id);
export const empty = () => ({type: 'FeatureCollection', features: []});
export const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

/** Small element builder; children are nodes or strings (inserted as text, never HTML). */
export function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') element.className = value;
    else if (key === 'style') element.style.cssText = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else element.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

// ---------------------------------------------------------------- HTTP

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function describe(data, response) {
  const detail = data && typeof data === 'object' ? data.detail : null;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI request-validation errors: [{loc, msg, type}, ...]
    return detail.map(item => {
      const where = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
      return where ? `${where}: ${item?.msg ?? ''}` : String(item?.msg ?? JSON.stringify(item));
    }).join('; ');
  }
  return `Erro HTTP ${response.status} em ${new URL(response.url).pathname}`;
}

export async function get(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch { if (response.ok) throw new ApiError(`Resposta inválida de ${url}`, response.status); }
  }
  if (!response.ok) throw new ApiError(describe(data, response), response.status);
  return data;
}

export const post = (url, payload, options = {}) =>
  get(url, {...options, method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});

export const isAbort = (error) => error?.name === 'AbortError';

// ---------------------------------------------------------------- status bar

export function status(message, kind = 'info') {
  const bar = $('statusbar');
  bar.dataset.kind = kind;
  $('status-text').textContent = message;
}

// ---------------------------------------------------------------- formatting

const integer = new Intl.NumberFormat('pt-BR', {maximumFractionDigits: 0});
const decimal = new Intl.NumberFormat('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const compact = new Intl.NumberFormat('pt-BR', {notation: 'compact', maximumFractionDigits: 1});
const money = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL', maximumFractionDigits: 0});
const moneyCompact = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL', notation: 'compact', maximumFractionDigits: 1});

export const fmt = {
  int: (value) => Number.isFinite(value) ? integer.format(value) : '—',
  dec: (value) => Number.isFinite(value) ? decimal.format(value) : '—',
  compact: (value) => Number.isFinite(value) ? (Math.abs(value) < 10_000 ? integer.format(value) : compact.format(value)) : '—',
  money: (value) => Number.isFinite(value) ? money.format(value) : '—',
  moneyCompact: (value) => Number.isFinite(value) ? (Math.abs(value) < 10_000 ? money.format(value) : moneyCompact.format(value)) : '—',
  num: (value, digits = 2) => Number.isFinite(value)
    ? new Intl.NumberFormat('pt-BR', {maximumFractionDigits: digits}).format(value) : '—',
};

// ---------------------------------------------------------------- colour tokens

/** Colours live once, as CSS custom properties; MapLibre paint reads them here. */
export const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

// ---------------------------------------------------------------- vocabulary

// Order is the stacking order of the farm-status chart (bottom → top).
export const STATUSES = [
  {key: 'healthy', label: 'Saudável', plural: 'Saudáveis'},
  {key: 'quarantined', label: 'Em quarentena', plural: 'Em quarentena'},
  {key: 'hidden', label: 'Infectada oculta', plural: 'Ocultas'},
  {key: 'notified', label: 'Notificada', plural: 'Notificadas'},
  {key: 'culled', label: 'Sacrificada', plural: 'Sacrificadas'},
  // Partly restocked after the sanitary void, under monitoring (official protocol).
  {key: 'restocked', label: 'Repovoada', plural: 'Repovoadas'},
];
export const statusLabel = (key) => STATUSES.find(s => s.key === key)?.label ?? key;

// Introduction pathways: one hue each, shared by charts, risk layer and map.
// `transmission` is the pathway name in /api/sim/transmissions records.
export const PATHWAYS = [
  {key: 'fomite', event: 'INTRODUCAO_FOMITE', label: 'Caminhão (fômite)', risk: 'fomite', transmission: 'fomite'},
  {key: 'local', event: 'INTRODUCAO_VIZINHANCA', label: 'Vizinhança', risk: 'local', transmission: 'local'},
  {key: 'birds', event: 'INTRODUCAO_AVES_MIGRATORIAS', label: 'Aves silvestres', risk: 'bird', transmission: 'bird'},
];

export const SEIR = [
  {key: 'susceptible', label: 'Suscetíveis'},
  {key: 'exposed', label: 'Expostas'},
  {key: 'infected', label: 'Infectadas'},
  {key: 'recovered', label: 'Recuperadas'},
  {key: 'dead', label: 'Mortas'},
];

export const HUB_KINDS = {
  feed_mill: 'Fábrica de ração', slaughterhouse: 'Abatedouro', egg_grading: 'Entreposto de ovos',
  farm: 'Granja', producer: 'Produtor', farm_layer: 'Granja de postura', farm_broiler: 'Granja de corte',
};
export const kindLabel = (kind) => HUB_KINDS[kind] ?? kind ?? '—';

// Farm production type (`production` in /api/sim/farms); null when the facility kind does not say.
export const PRODUCTIONS = [
  {key: 'postura', kind: 'farm_layer', label: 'Postura', plural: 'de postura', note: 'poedeiras, ovos'},
  {key: 'corte', kind: 'farm_broiler', label: 'Corte', plural: 'de corte', note: 'frangos'},
];

// Product lots (/api/sim/state `lots`, /api/sim/lots): quantity units per product.
export const PRODUCTS = [
  {key: 'ovos', label: 'Ovos', unit: 'dúzias'},
  {key: 'aves', label: 'Aves vivas', unit: 'aves'},
];

export const EVENT_LABELS = {
  INTRODUCAO_INICIAL: 'Introdução inicial',
  INTRODUCAO_FOMITE: 'Introdução por caminhão',
  INTRODUCAO_VIZINHANCA: 'Transmissão vizinha',
  INTRODUCAO_AVES_MIGRATORIAS: 'Introdução por aves silvestres',
  INFECCAO: 'Novas exposições',
  FIM_INFECCAO: 'Fim da infecção',
  DETECCAO: 'Detecção',
  REACAO_AUTOMATICA: 'Bloqueio automático',
  LIBERACAO_QUARENTENA: 'Fim da quarentena',
  SACRIFICIO_SANITARIO: 'Sacrifício sanitário',
  QUARENTENA_PERIFERICA: 'Quarentena perimetral',
  BLOQUEIO_MOVIMENTACAO: 'Bloqueio de movimentação',
  MELHORIA_BIOSSEGURANCA: 'Melhoria de biosseguridade',
  INFECCAO_AVES_SILVESTRES: 'Infecção de aves silvestres',
  CHEGADA_BANDO_INFECTADO: 'Bando infectado chegou',
  AVISO_AVES: 'Aviso das aves silvestres',
  AVISO_FROTA: 'Aviso da frota',
  FOCO_CONFIRMADO: 'Foco confirmado',
  DESPOVOAMENTO: 'Despovoamento do foco',
  INTERDICAO_TRANSITO: 'Trânsito interrompido',
  VIGILANCIA_ATIVA: 'Detecção por vigilância ativa',
  VAZIO_SANITARIO: 'Vazio sanitário',
  REPOVOAMENTO: 'Repovoamento parcial',
  REPOVOAMENTO_TOTAL: 'Repovoamento total',
  RECOLHIMENTO_LOTES: 'Recolhimento de lotes',
};
// Emergency-area rings of the official transit protocol, most restrictive first.
// `radius` is the ModelParams field holding the ring's outer radius.
export const ZONE_RINGS = [
  {key: 'perifoco', label: 'Perifoco', radius: 'perifocus_radius_km'},
  {key: 'vigilancia', label: 'Vigilância', radius: 'surveillance_radius_km'},
  {key: 'protecao', label: 'Proteção', radius: 'protection_radius_km'},
];
export const zoneLabel = (key) => ZONE_RINGS.find(r => r.key === key)?.label ?? key;
// The documents behind the protocol parameter group (no stable public URL is
// known); each parameter cites its own page in its source badge.
export const PROTOCOL_SOURCES = [
  'MAPA, Plano de Contingência para Emergências Zoossanitárias, parte específica Influenza Aviária de '
    + 'Alta Patogenicidade e Doença de Newcastle, versão 1.0, junho de 2023',
  'MAPA, Procedimento Operacional Padrão para Postos Fixos e Equipes Volantes em Emergência Zoossanitária, '
    + 'parte específica IAAP e DNC, versão 1.0, novembro de 2023',
];

export const eventLabel = (type) => EVENT_LABELS[type] ?? type;

// Timeline filter chips. "Curso da infecção" holds the per-farm daily INFECCAO
// noise; it starts off so introductions and reactions stay readable.
export const EVENT_CATEGORIES = [
  {key: 'intro', label: 'Introduções', on: true,
    types: ['INTRODUCAO_INICIAL', 'INTRODUCAO_FOMITE', 'INTRODUCAO_VIZINHANCA', 'INTRODUCAO_AVES_MIGRATORIAS']},
  {key: 'detect', label: 'Detecção e reação', on: true,
    types: ['DETECCAO', 'REACAO_AUTOMATICA', 'LIBERACAO_QUARENTENA', 'QUARENTENA_PERIFERICA']},
  {key: 'action', label: 'Intervenções', on: true,
    types: ['SACRIFICIO_SANITARIO', 'BLOQUEIO_MOVIMENTACAO', 'MELHORIA_BIOSSEGURANCA',
      'FOCO_CONFIRMADO', 'DESPOVOAMENTO', 'INTERDICAO_TRANSITO', 'VIGILANCIA_ATIVA', 'RECOLHIMENTO_LOTES']},
  {key: 'restock', label: 'Pós-foco', on: true, types: ['VAZIO_SANITARIO', 'REPOVOAMENTO', 'REPOVOAMENTO_TOTAL']},
  {key: 'birds', label: 'Aves silvestres', on: true, types: ['INFECCAO_AVES_SILVESTRES', 'CHEGADA_BANDO_INFECTADO']},
  {key: 'course', label: 'Curso da infecção', on: false, types: ['INFECCAO', 'FIM_INFECCAO']},
  {key: 'warn', label: 'Avisos', on: true, types: ['AVISO_AVES', 'AVISO_FROTA']},
];
const categoryByType = new Map(EVENT_CATEGORIES.flatMap(c => c.types.map(t => [t, c.key])));
// Unknown future event types are shown with the warnings rather than hidden.
export const eventCategory = (type) => categoryByType.get(type) ?? 'warn';
