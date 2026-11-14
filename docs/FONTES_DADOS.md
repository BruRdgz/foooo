# Fontes de dados: granjas e bases sintéticas ancoradas em dados oficiais

Quando o mundo carregado não tem `facilities.parquet` (caso do mundo de
Pernambuco atual), a sessão de simulação cria uma população **sintética** de
granjas e bases (`install_official_facilities` em
`src/simgeope/simulation/model.py`). Nenhuma granja do modelo corresponde a
uma granja real: os dados oficiais definem quantas granjas existem de cada
tipo, quantas aves cada uma tem e em que municípios elas tendem a estar.

## Arquivos e reprodução

- Tabela derivada, versionada como package data:
  `src/simgeope/simulation/reference/producao_municipios_pe.csv`
  (185 municípios de PE, só contagens agregadas por município).
- Script que a gera a partir dos arquivos brutos (fora do repositório):

  ```
  python tools/build_reference_data.py PPM.json tabela74.csv ADAGRO.pdf
  ```

  Requer `pdftotext` (poppler-utils) para ler o PDF da ADAGRO. O script
  imprime quantos registros da ADAGRO foram lidos, quantos precisaram de
  casamento por prefixo e lista (e termina com erro) os que não casarem com
  nenhum município do IBGE; nenhum é descartado em silêncio.

### Colunas da tabela

| Coluna | Origem |
|---|---|
| `codigo_ibge` | Código IBGE do município (7 dígitos) |
| `municipio` | Nome IBGE |
| `galinaceos_2024` | PPM 2024, tabela 3939, "Galináceos - total" (cabeças em 31/12) |
| `galinhas_2024` | PPM 2024, tabela 3939, "Galináceos - galinhas" |
| `corte_proxy_2024` | `galinaceos_2024 − galinhas_2024` |
| `ovos_galinha_mil_duzias_2023` | SIDRA tabela 74, ovos de galinha em 2023 (mil dúzias) |
| `granjas_adagro_2022` | Registros de granjas avícolas da ADAGRO por município (fev/2022) |

## Fontes

1. **IBGE, Pesquisa da Pecuária Municipal (PPM) 2024, tabela 3939** —
   efetivo dos rebanhos em 31/12/2024, por município de PE, obtido pela API
   de agregados v3:
   `https://servicodados.ibge.gov.br/api/v3/agregados/3939/periodos/2024/variaveis/105?localidades=N6[N3[26]]&classificacao=79[32796,32793]`.
   Totais de PE: 68.659.633 galináceos, 16.263.407 galinhas, logo
   52.396.226 no proxy de corte.
2. **IBGE/SIDRA, tabela 74** — produção de origem animal (ovos de galinha,
   mil dúzias), por município, até 2023. Usada só como checagem cruzada
   (abaixo). Células "..." ou "X" do IBGE ficariam vazias; "-" é zero.
3. **IBGE, Pesquisa da Produção de Ovos de Galinha (POG), tabela 7524, 1º
   trimestre de 2025, PE** — 62 estabelecimentos com pelo menos 10 mil
   galinhas poedeiras (55 de ovos para consumo, 7 de ovos para incubação) e
   13.352.483 galinhas poedeiras. A POG só é publicada por UF, por isso não
   entra na tabela municipal; os números estão citados em `model.py`.
4. **ADAGRO, granjas avícolas registradas, fevereiro de 2022** — PDF
   "GRANJA_AVÍCOLA_DE_CORTE_-_FEVEREIRO_2022.pdf". O PDF "de postura" traz os
   mesmos 267 registros (mesmos números de registro), então a lista **não
   distingue postura de corte**. O PDF contém nomes de pessoas: o repositório
   guarda apenas a contagem de registros por município. Resultado: 267
   registros em 45 municípios (Lajedo 65, Garanhuns 21, São Caitano 19, São
   Bento do Una 16, Correntes 13, Ibirajuba 13, São José do Egito 12,
   Tacaimbó 12, Cachoeirinha 10, ...).
   - Casamento de nomes: maiúsculas sem acento. O `pdftotext` às vezes junta
     o nome do proprietário à coluna do município (ex.: "CHA DE ALEGRIA
     <NOME> ..."), então o município é o nome IBGE mais longo que prefixa o
     texto da coluna (25 registros casaram assim: Chã de Alegria, Glória do
     Goitá, Lagoa do Ouro, São Bento do Una, São José do Egito).
   - Grafia diferente do IBGE: "SAO CAETANO" = São Caitano (2613107), por
     alias explícito no script.
5. **Bases (fábricas de ração, abatedouros, entrepostos de ovos)** — não há
   lista aberta acessível (o portal de dados do MAPA recusou conexão). As
   bases são posicionadas por suposição, descrita abaixo.

## Como a população sintética é montada

| Item | Valor | Origem |
|---|---|---|
| Granjas de postura (`farm_layer`, ids `P0001`...) | 62 | POG 1T2025 |
| Aves por granja de postura | 215.363 | 13.352.483 / 62 (POG) |
| Granjas de corte (`farm_broiler`, ids `C0001`...) | 205 | **suposição**: 267 registros ADAGRO − 62 de postura |
| Aves por granja de corte | 255.591 | **suposição**: 52.396.226 (proxy PPM) / 205; limite superior |
| Município de cada granja de postura | sorteado ∝ `galinhas_2024` | PPM 2024 |
| Município de cada granja de corte | sorteado ∝ `corte_proxy_2024` | PPM 2024 |
| Ponto dentro do município | uniforme por área, ligado à malha viária | suposição de modelagem |
| Bases por frota | 2 por tipo de base | **suposição** (mesmo valor da versão anterior) |
| Município de cada base | sorteado ∝ soma dos pesos dos tipos de granja que a frota atende | **suposição** |

Detalhes:

- Todas as granjas de um tipo têm a média do tipo: nenhuma fonte dá a
  distribuição de tamanho.
- O ponto é rejeitado e re-sorteado (inclusive o município) se ficar fora do
  estado, a mais de `max_snap_distance_m` de uma estrada ou fora da maior
  componente fortemente conexa da malha viária (os caminhões precisam ir e
  voltar). Isso desloca levemente a distribuição para municípios com
  estradas bem conectadas; Fernando de Noronha, isolado, nunca recebe granja.
- Frotas padrão: a fábrica de ração (`feed_mill`) atende todas as granjas e
  é posicionada ∝ galináceos totais (postura + corte); o abatedouro
  (`slaughterhouse`) atende granjas de corte (e de tipo desconhecido, `farm`
  e `producer`, em mundos sem tipo) e é posicionado ∝ proxy de corte.
  Qualquer outro tipo de base presente em `ModelParams.fleets` recebe bases
  pela mesma regra (ex.: um entreposto de ovos que atenda só `farm_layer`
  fica ∝ galinhas).
- Se o mundo não tiver algum município com peso positivo na tabela, a
  instalação falha com erro (`World lacks ... municipalities`); não há
  recuo silencioso para os polos ilustrativos (`DEMO_POULTRY_POLES`, que
  continuam disponíveis em `install_synthetic_facilities` para testes e
  demonstrações).

## Checagem cruzada da postura

A participação de cada município nas galinhas da PPM 2024 acompanha a
produção de ovos de 2023 da tabela 74 (correlação de Pearson 0,996 entre as
duas colunas nos 185 municípios):

| Município | Galinhas 2024 (PPM) | Ovos 2023 (tab. 74) |
|---|---|---|
| São Bento do Una | 36,5% | 38,4% |
| Paudalho | 12,7% | 11,9% |
| Orobó | 8,6% | 9,2% |
| Caruaru | 3,4% | 5,4% |

Por isso a postura usa a PPM (mesmo ano e mesma fonte do proxy de corte) e
a tabela 74 fica só como coluna de conferência.

## Limitações

- **Proxy de corte**: "galináceos − galinhas" inclui galos, frangas, pintos e
  criação de quintal, não só frangos de corte; o tamanho de 255.591 aves por
  granja de corte é um limite superior, e municípios com muita criação de
  quintal recebem peso a mais. A correlação entre o proxy e os registros da
  ADAGRO por município é moderada (Pearson 0,50); 76% do proxy está nos 45
  municípios com registro na ADAGRO.
- **Contagem de corte**: a lista da ADAGRO é de 2022, não separa os tipos e
  pode não incluir todas as granjas em atividade; a POG é de 2025 e só
  conta postura com ≥ 10 mil aves. Subtrair uma da outra é suposição.
- **Bases**: posição, quantidade e capacidade das bases são suposições; com
  267 granjas e 2 bases por frota, cada caminhão atende muito mais granjas
  que na população anterior (120 granjas), e algumas granjas do Sertão
  ficam fora do alcance de um turno (evento `AVISO_FROTA`).
- **Escala**: com ~215–256 mil aves por granja (antes 5.000), o custo de
  abate (`cull_cost_per_bird` × aves) passa a dominar o custo acumulado, e
  uma granja precisa de mais aves infectadas para carregar um caminhão por
  completo (`fomite_full_load_prevalence` é uma prevalência). Os parâmetros
  ilustrativos do modelo não foram recalibrados para essa escala. Nas
  rodadas de 120 dias com as sementes 1–10 no mundo de Pernambuco, o preset
  "surto_sustentado" manteve ≥ 5 granjas infectadas em 4/10 sementes (antes
  7/10) e, no "cadeia_logistica", os caminhões causaram 1 de 18 introduções
  (antes a maioria): os presets precisam ser recalibrados.
