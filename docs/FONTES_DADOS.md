# Fontes de dados do modelo

Registro das fontes usadas para parametrizar a simulação, do que foi tirado de
cada uma e do que é suposição.

## Granjas e bases sintéticas ancoradas em dados oficiais

Quando o mundo carregado não tem `facilities.parquet` (caso do mundo de
Pernambuco atual), a sessão de simulação cria uma população **sintética** de
granjas e bases (`install_official_facilities` em
`src/simgeope/simulation/model.py`). Nenhuma granja do modelo corresponde a
uma granja real: os dados oficiais definem quantas granjas existem de cada
tipo, quantas aves cada uma tem e em que municípios elas tendem a estar.

### Arquivos e reprodução

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

#### Colunas da tabela

| Coluna | Origem |
|---|---|
| `codigo_ibge` | Código IBGE do município (7 dígitos) |
| `municipio` | Nome IBGE |
| `galinaceos_2024` | PPM 2024, tabela 3939, "Galináceos - total" (cabeças em 31/12) |
| `galinhas_2024` | PPM 2024, tabela 3939, "Galináceos - galinhas" |
| `corte_proxy_2024` | `galinaceos_2024 − galinhas_2024` |
| `ovos_galinha_mil_duzias_2023` | SIDRA tabela 74, ovos de galinha em 2023 (mil dúzias) |
| `granjas_adagro_2022` | Registros de granjas avícolas da ADAGRO por município (fev/2022) |

### Fontes

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

### Como a população sintética é montada

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

### Checagem cruzada da postura

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

### Limitações

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
  completo (`fomite_full_load_prevalence` é uma prevalência). Os presets
  ilustrativos foram recalibrados para essa população no mundo de
  Pernambuco (sementes 1–20 × 120 dias; números e escolhas no comentário de
  `PARAM_PRESETS` em `params.py` e em `docs/PROTOCOLO_OFICIAL.md`).

## Produção e mercados

O modelo **não** simula infecção humana por consumo: o Plano de Contingência
classifica "produtos de origem animal processados e embalados" como baixo
risco (Quadro 1, §6). Ele mede o que sai de granjas infectadas: quanto produto
foi expedido antes da notificação, quanto tempo ficou exposto até o
recolhimento e quanto foi recolhido. O Quadro 1 classifica "produtos e
subprodutos de quaisquer aves (carne, ovos, penas...)" como alto risco para
o trânsito.

### Fontes

- **Plano de Contingência MAPA, Parte Específica IAAP/DNC, v1.0, jun/2023**
  - §6, Quadro 1: categorias de risco para o trânsito (acima).
  - §7.1 (foco): destruição da carne, dos ovos e dos subprodutos "produzidos
    durante dois períodos de incubação da doença", conforme o Código
    Terrestre da OMSA; em reprodução, destruição dos ovos recebidos pelos
    incubatórios nos 28 dias anteriores à confirmação.
  - §7.2 e §7.3: na área perifocal e na de vigilância, ovos para consumo só
    vão a entreposto na área de emergência (ou fora dela, se avaliado pelo
    SVO); aves só para abate imediato/sanitário em abatedouro designado.
- **Ficha Técnica de Influenza Aviária (MAPA)**: incubação "de algumas horas
  até 14 dias". Dois períodos = 28 dias.
- **IBGE, Pesquisa da Produção de Ovos de Galinha, tabela 7524**, 1º trimestre
  de 2025, Pernambuco (estabelecimentos com 10 mil poedeiras ou mais):
  62 informantes, 13.352.483 poedeiras (média do plantel no fim de cada mês),
  79.752 mil dúzias no trimestre (consumo 78.199; incubação 1.553).
  Produtividade: 79.752.000 × 12 ÷ (13.352.483 × 90 dias) = 0,796
  ovo/poedeira/dia (jan + fev + mar de 2025 = 90 dias), arredondada para 0,8.

### Parâmetros (grupo "Produção e mercados")

| Parâmetro | Valor | Origem |
|---|---|---|
| `eggs_per_hen_day` | 0,8 ovo/poedeira/dia | IBGE, tabela 7524 (cálculo acima) |
| `broiler_lot_birds` | 5.000 aves por visita | **suposição**: uma carga de caminhão de frango vivo; não há dado aberto |
| `trace_window_days` | 28 dias | Plano §7.1 (dois períodos de incubação) + Ficha Técnica (até 14 dias) |

Frotas padrão (`ModelParams.fleets`, sem edição na interface):

- `slaughterhouse`: 2 caminhões por abatedouro, 1 parada por dia, atende
  `farm_broiler` e granjas de tipo desconhecido (`farm`, `producer`), para que
  mundos sem tipo de produção continuem iguais.
- `egg_grading` (entreposto de ovos): 2 caminhões por entreposto, 3 paradas
  por dia, atende só `farm_layer`. **Suposição**: não há dado aberto sobre a
  frequência de coleta nem o tamanho da frota.

Os caminhões de ovos usam o mesmo mecanismo de fômite das outras frotas:
recolhem carga viral numa granja de postura que elimina vírus e podem
introduzi-la na granja seguinte do roteiro, além da troca de carga com o
entreposto (`hub_transfer_fraction`, lavagem na base), que representa
bandejas e caixas que voltam contaminadas.

### Regras do modelo

- **Lote**: cada coleta de um caminhão de abatedouro ("aves") ou de
  entreposto ("ovos") numa granja gera um lote com dia, granja, base de
  destino, caminhão, quantidade e o estado da granja *antes* da visita:
  `hidden` (infectada sem notificação: **lote exposto**), `notified`,
  `healthy` ou `restocked`. Granjas em quarentena ou abatidas não são
  visitadas.
- **Ovos**: dúzias postas desde a última coleta de ovos da granja, com o
  plantel vivo no dia da coleta (S + E + I + R) × `eggs_per_hen_day` ÷ 12.
  A queda de postura em aves doentes não é modelada. Se a granja esteve
  infectada sem notificação em algum dia desde a última coleta, o lote de
  ovos é exposto mesmo que a infecção já tenha acabado (salvo se a granja
  estiver notificada na coleta). Suposição: os ovos postos durante a
  quarentena são destruídos na granja; a contagem recomeça na liberação. No
  repovoamento total, os dias anteriores são contados com o plantel cheio.
- **Aves**: `broiler_lot_birds` por visita, limitado ao plantel vivo. O modelo
  não simula ciclos de produção e não retira essas aves do plantel.
- **Rastreamento**: com o protocolo oficial ligado, o gatilho é a
  confirmação do foco (`FOCO_CONFIRMADO`); desligado, a notificação
  (`DETECCAO`). São recolhidos os lotes da granja coletados a partir de
  (dia do gatilho − `trace_window_days`), com o evento `RECOLHIMENTO_LOTES`.
  Enquanto a granja seguir infectada e não abatida, os lotes que ela ainda
  expedir (por exemplo, notificada antes da quarentena) são recolhidos no
  próprio dia.
- **Fora da janela**: lotes expostos coletados antes do início da janela não
  são recolhidos e são contados à parte (inclui lotes de uma infecção
  anterior, não detectada, da mesma granja).
- **Dias de exposição**: dia do recolhimento − dia da coleta, em média sobre
  os lotes expostos recolhidos.

### Fora do modelo por falta de dado

- Destino dos produtos após o entreposto ou o abatedouro (varejo,
  processamento, outros estados).
- Tamanho real dos lotes e frequência de coleta.
- Feiras e comércio de aves vivas.
- Sobrevivência do vírus em ovos, bandejas e embalagens (além do fômite
  genérico dos caminhões).
- Ovos férteis e incubatórios (a regra dos 28 dias para reprodução coincide
  com a janela, mas incubatórios não são modelados).
- Custo do recolhimento: não há base de custo; não entra no custo acumulado.

## Aves migratórias

Código: `MIGRATORY_ARRIVAL_PROFILE`, `SITE_ANCHOR_MAX_M`, `load_migratory_sites` e
`AgentModel._bird_arrivals` em `src/simgeope/simulation/model.py`; parâmetros
`migratory_flocks_per_day` e `migratory_site_fraction` (grupo "Aves silvestres"
de `params.py`); tabela `src/simgeope/simulation/reference/migracao_sitios_pe.csv`.

### Fontes

- **Azevedo Jr., S. M. 1992.** Anilhamento de aves migratórias na Coroa do
  Avião, Igarassu, Pernambuco, Brasil. *Cad. Ômega Univ. Fed. Rural PE, Sér.
  Ciênc. Aquát.* 3: 31-47. Fonte primária para Pernambuco: limícolas árticas
  (*Pluvialis squatarola*, *Arenaria interpres*, *Calidris pusilla*, *C. alba*)
  de agosto a abril; *Sterna hirundo* de agosto ao início de novembro, com pico
  em outubro; *S. eurygnatha* de setembro a novembro. Contagens máximas de
  30/10 a 03/11/1989 (178 *P. squatarola*, 162 *A. interpres*, 320 *C. alba*,
  120 *S. hirundo*). Em maio–julho só poucos indivíduos jovens ou subadultos
  (9, 18 e 16 aves). Cerca de 3.000 *C. pusilla* nos mangues do Complexo de
  Salgadinho (Olinda) em março de 1989.
- **CEMAVE/ICMBio, Relatório de rotas e áreas de concentração de aves
  migratórias no Brasil, 3ª ed., 2019.** Neárticas "permanecem no Brasil de
  setembro a maio" (p. 17); Coroa do Avião citada como área importante, com
  mais de 400 *C. alba* (Cardoso & Nascimento 2007).
- **Resumo do relatório CEMAVE feito pelo grupo** (`2. Nordeste_.docx`):
  Parque dos Manguezais (Recife), cerca de 3.000 *C. pusilla* (W. Telino-Jr,
  com. pess. 2021).
- **Resumo de rotas feito pelo grupo** (`1.2 - RESUMO ROTAS - BRASIL.docx`):
  migrantes do inverno boreal chegam "geralmente em setembro" e ficam "até
  abril"; em junho–julho só jovens e não reprodutores.
- **MAPA, Plano de Contingência, parte específica de influenza aviária e
  Newcastle, §13.1.** Espécies mais prováveis de gerar alerta de IAAP:
  vira-pedras, maçarico-branco, maçarico-de-papo-vermelho,
  maçarico-de-sobre-branco, maçarico-rasteirinho, falcão-peregrino,
  trinta-réis-boreal, entre outras. O perfil mensal segue essas espécies
  (migrantes do inverno boreal).
- **Compilação do grupo** (`2.1 - Nordeste (PERNAMBUCO).docx`): lista de sítios
  em PE. É fonte secundária (cita sites como folhape.com.br, uema.br) e não
  traz contagens; os sítios que vêm só dela entram com o peso mínimo.

### Perfil mensal de chegada

Fração da taxa de pico (`migratory_flocks_per_day`) aplicada a cada mês.

| Mês | Fator | Fonte e regra |
|---|---|---|
| jan, fev, mar | 0,5 | presença de invernada documentada (Azevedo Jr. 1992; 3.000 *C. pusilla* em março), sem série mensal |
| abr | 0,25 | último mês da temporada ("agosto a abril"; "até abril") |
| mai, jun, jul | 0 | reprodução no Ártico; as poucas aves contadas são jovens que ficaram, não chegadas novas |
| ago | 0,25 | primeiro mês da temporada na Coroa do Avião |
| set, out, nov | 1 | pico de chegada: "chegam geralmente em setembro"; todos os máximos de 1989 entre 30/10 e 03/11; *S. hirundo* com pico em outubro |
| dez | 0,5 | presença de invernada, como jan–mar |

Os meses vêm das fontes; os níveis 0,25 e 0,5 são **suposição** (as fontes dão
meses de presença, não taxas). A taxa de pico padrão, 0,7 bando por dia, também
é **suposição**: as fontes não dão uma taxa de chegada, então ela mantém o total
anual da taxa sem sazonalidade usada antes (0,3 por dia o ano todo, cerca de
110 bandos por ano; com o perfil, 0,7 × 166,75 dias-equivalentes de pico ≈ 117).
No pico a população migratória fica perto de 0,7 × 8 aves × 30 dias ≈ 170 aves. O CEMAVE 2019 diz "setembro a maio" para o país;
o modelo segue a fonte de Pernambuco (agosto a abril), e a presença em maio
vem das aves que chegaram em abril e ainda não partiram.

A permanência (`bird_mean_stay_days`) **não** foi tornada sazonal. Com chegadas
zeradas de maio a julho e permanência média de 30 dias, a população migratória
cai para perto de zero no inverno austral, como nas fontes, sem novo
parâmetro. Uma permanência de temporada inteira (setembro a abril) exigiria
separar migrantes de residentes no `BirdAgent` (hoje a mesma regra de partida
vale para todas as aves) e multiplicaria a população várias vezes,
rompendo o teto de cerca de 1.200 aves que os limites dos parâmetros garantem.
A rotatividade de 30 dias também representa o movimento das aves entre
sítios próximos descrito por Azevedo Jr. (Canal de Santa Cruz, Coroa,
Maria Farinha, Itamaracá).

### Sítios de pouso conhecidos

`migratory_site_fraction` (padrão 0,5, **suposição**) dos bandos pousa num
sítio da tabela, sorteado pelo peso; o resto pousa no sorteio por tamanho do
corpo d'água em todo o estado, que representa sítios não inventariados
(açudes do interior, rio São Francisco). Com fração 0 o modelo repete
exatamente o sorteio anterior.

| Sítio | Município (IBGE) | Coordenada | Contagem máxima | Peso |
|---|---|---|---|---|
| Parque dos Manguezais | Recife (2611606) | sem coordenada | ~3.000 *C. pusilla* (CEMAVE, com. pess. 2021) | 3000 |
| Complexo de Salgadinho | Olinda (2609600) | sem coordenada | ~3.000 *C. pusilla*, mar/1989 (Azevedo Jr. 1992) | 3000 |
| Ilha da Coroa do Avião | Igarassu (2606804) | aproximada, pt.wikipedia | >400 *C. alba* (CEMAVE 2019) | 400 |
| Lagoa do Araçá | Recife (2611606) | aproximada, pt.wikipedia (coincide com a lagoa no OSM) | sem contagem | 100 |
| Lagoa do Olho d'Água | Jaboatão dos Guararapes (2607901) | aproximada, ponto do polígono OSM | sem contagem | 100 |
| Barragem de Santa Cruz do Capibaribe | Santa Cruz do Capibaribe (2612505) | sem coordenada | sem contagem | 100 |

- **Peso:** a maior contagem de uma espécie registrada no sítio. Sem contagem,
  peso mínimo de 100 (**suposição**: um quarto da menor contagem documentada
  em PE, os 400 *C. alba* da Coroa do Avião).
- **Coordenada da Coroa do Avião:** Azevedo Jr. 1992 dá 7°40'S 34°50'W, mas
  esse ponto cai em Goiana, no extremo norte do Canal de Santa Cruz, e o
  próprio artigo situa a ilhota na Barra Sul do canal. Usamos 7°48'58,5"S
  34°50'21"O (pt.wikipedia), em Igarassu.
- **Pouso:** o bando de um sítio com coordenada pousa no ponto de água mapeada
  mais próximo (rio, canal ou corpo d'água), se estiver no estado e a até 3 km
  (`SITE_ANCHOR_MAX_M`); no mundo de Pernambuco a Coroa do Avião fica a cerca
  de 650 m da água mapeada e as duas lagoas ficam sobre a própria lagoa. Sem
  coordenada, ou se a âncora falhar, o bando pousa num ponto de água mapeada
  dentro do município, sorteado pelo tamanho (ponto qualquer do município se
  não houver água mapeada). Sítios de municípios ausentes do mundo são
  ignorados. Cada desvio é registrado como evento `AVISO_AVES`.
- **Fora da tabela:**
  - Refúgio Charles Darwin (Igarassu): a compilação só diz "Registrar presença
    das mesmas espécies…", sem registro nem contagem, e o refúgio é mata, não
    área úmida.
  - Alagado de São Bento do Una: o registro é de cabeça-seca (*Mycteria
    americana*), que não é migrante do inverno boreal nem está na lista de
    alerta do §13.1; o perfil mensal não se aplica a ela.
  - Sertão do São Francisco: sem município nem espécie definidos.
