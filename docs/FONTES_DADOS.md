# Fontes de dados do modelo

Registro das fontes usadas para parametrizar a simulação, do que foi tirado de
cada uma e do que é suposição.

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
meses de presença, não taxas). O CEMAVE 2019 diz "setembro a maio" para o país;
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
