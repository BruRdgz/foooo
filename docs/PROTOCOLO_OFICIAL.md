# Protocolo oficial de trânsito (MAPA POP 2023) no SimGeoPE

Registro do que o modelo implementa a partir do POP, do que é suposição e do que
falta confirmar com a ADAGRO. Atualize este arquivo (e o `source`/`assumption` do
parâmetro em `src/simgeope/simulation/params.py`) quando a resposta chegar.

**Fonte:** MAPA, *Procedimento Operacional Padrão para Postos Fixos e Equipes
Volantes em Emergência Zoossanitária — Parte específica: IAAP e DNC*, v1.0,
nov/2023. O documento trata **apenas de trânsito**.

## O que o POP diz

| Página | Conteúdo |
|---|---|
| p. 2 | "Em um momento inicial, a movimentação de animais e produtos de risco ... deve ser interrompida em toda área de emergência"; depois, "suspensões gradativas". Do foco, nenhuma movimentação "exceto com finalidade de destruição". |
| p. 2 item 3; p. 8 §7 | Veículos limpos e desinfetados "antes e após cada carregamento, na entrada e saída dos estabelecimentos e nos perímetros das diferentes áreas de risco". |
| p. 2 item 4 | Veículos "de uso exclusivo da área de emergência" sempre que possível. |
| p. 2 itens 5 e 7 | Rotas aprovadas pelo SVO; veículos lacrados. |
| p. 3 | Zonas: "Perifoco (área de 3km)", "Área de vigilância (área de 7km)", "Área de proteção (área de 15km)". |
| p. 4, Quadro 2 (aves vivas) | Perifoco: ingresso "Proibido"; saída para abate "mediante exame clínico das aves". Vigilância: ingresso/saída por avaliação do SVO; saída para abate após exame clínico. |
| p. 7, Quadro 4 (ração) | Perifoco e vigilância: ingresso autorizado sob supervisão do SVO, com desinfecção do veículo, rota e horário definidos, veículo de preferência exclusivo da área. Proteção: por rota aprovada pelo SVO. |

**O POP não diz:** se os raios são cumulativos, a duração da interrupção
inicial, o prazo de confirmação laboratorial, se o foco é despovoado
automaticamente, o critério de liberação das zonas, os prazos de vigilância
ativa, nem a eficácia da desinfecção.

## Como o modelo aplica (com `protocol_enabled = 1`)

Desligado por padrão: com `protocol_enabled = 0` as trajetórias com semente são
idênticas às de antes. O estado das zonas é calculado ao fim de cada dia e vale
para os caminhões e para a vigilância do dia seguinte.

| Regra do modelo | Base no POP |
|---|---|
| Granja notificada vira **foco confirmado** `confirmation_days` depois (evento `FOCO_CONFIRMADO`), mesmo que a infecção acabe antes (as amostras já foram colhidas). | Suposição (o POP parte de foco já confirmado). |
| Com `auto_depopulation = 1`, o foco é despovoado no dia da confirmação (`DESPOVOAMENTO`): mesmo custo e contabilidade do sacrifício sanitário manual, **sem** a quarentena dos vizinhos em 5 km. A intervenção manual não muda. | p. 2: saída do foco só para destruição; despovoamento automático é suposição. |
| Cada foco abre perifoco (≤ `perifocus_radius_km`), vigilância (≤ `surveillance_radius_km`) e proteção (≤ `protection_radius_km`), em linha reta a partir da granja-foco (CRS métrico). A zona de uma granja é a mais restritiva entre os focos ativos. Raios medidos a partir do foco, estritamente crescentes. | p. 3 (raios: ver suposições). |
| A área fica ativa até `zone_release_days` depois do despovoamento (ou do fim da infecção, se não houver despovoamento). | Suposição. |
| **Interrupção do trânsito:** por `standstill_days` dias depois da confirmação, nenhum caminhão para em granjas dentro do raio de proteção do foco (`INTERDICAO_TRANSITO`, uma vez por foco). A granja perde a visita (a rotação avança). | p. 2 ("momento inicial"); a duração é suposição. |
| Depois disso, toda parada em granja do perifoco ou da vigilância é seguida de desinfecção (probabilidade `zone_disinfection_prob`: carga → 0, origem apagada); cruzar o limite da proteção (entre paradas ou na volta à base) também desinfeta. | p. 2 item 3; p. 8 §7. |
| Caminhões de abatedouro (`slaughterhouse`) não param em granjas **notificadas** do perifoco (reprovam no exame clínico); granjas com infecção oculta passam. Caminhões de ração continuam. | p. 4 Quadro 2; p. 7 Quadro 4. |
| **Veículo exclusivo:** depois de parar dentro da proteção, o caminhão não faz paradas fora dela no mesmo dia; a próxima granja de fora fica para o dia seguinte. | p. 2 item 4; p. 7 Quadro 4. |
| **Vigilância ativa:** uma granja infectada e ainda não notificada no perifoco/vigilância é detectada até `perifocus_detection_days`/`surveillance_detection_days` depois do mais tardio entre a entrada na zona e o início da infecção (`VIGILANCIA_ATIVA`). Se o dia sorteado para a detecção for anterior, ele vale. | Suposição. |

## Suposições a confirmar

Valores usados no cenário "Protocolo oficial (MAPA)" (`protocolo_mapa`).

| Parâmetro | Valor adotado | Justificativa | Status | Pergunta à ADAGRO |
|---|---|---|---|---|
| `perifocus_radius_km` | 3 km | Texto do POP (p. 3). | Do POP | — |
| `surveillance_radius_km` | 10 km | A "área de 7 km" lida como anel além do perifoco (3 + 7), como na IN 32/2002. | a confirmar com ADAGRO | Os 7 km da área de vigilância contam a partir do foco ou a partir do limite do perifoco? |
| `protection_radius_km` | 25 km | A "área de 15 km" lida como anel adicional (10 + 15). Leitura alternativa: raio de 15 km. | a confirmar com ADAGRO | O raio externo da área de proteção é 15 km ou 25 km a partir do foco? |
| `confirmation_days` | 2 dias | Tempo típico de RT-PCR depois da notificação. | a confirmar com ADAGRO | Quantos dias, em média, entre a notificação e a confirmação laboratorial do foco? |
| `auto_depopulation` | 1 (sim) | Política brasileira de sacrifício sanitário para IAAP em plantéis comerciais. | a confirmar com ADAGRO | Todo foco confirmado em granja comercial é despovoado? Em quanto tempo depois da confirmação? |
| `standstill_days` | 7 dias | O POP fala em "momento inicial" sem dizer a duração. | a confirmar com ADAGRO | Quanto dura a interrupção inicial do trânsito em toda a área de emergência? |
| `zone_disinfection_prob` | 0,95 | Eficácia presumida da limpeza e desinfecção por parada. | a confirmar com ADAGRO | Há dado de eficácia da desinfecção de veículos nos postos fixos/volantes? |
| `zone_release_days` | 28 dias | Padrão OMSA: 28 dias depois de despovoar e limpar o foco (28 d também aparece no POP para a quarentena de pintos). | a confirmar com ADAGRO | Qual o critério e o prazo para liberar perifoco, vigilância e proteção? |
| `perifocus_detection_days` | 3 dias | Prazo presumido da visita de vigilância ativa no perifoco. | a confirmar com ADAGRO | Em quantos dias as granjas do perifoco recebem a visita de vigilância ativa? Ela se repete? |
| `surveillance_detection_days` | 7 dias | Idem, na área de vigilância. | a confirmar com ADAGRO | Idem para a área de vigilância. |
| Raio contado do foco ou do perímetro da granja | Do ponto da granja-foco | A geometria das granjas não existe no modelo. | a confirmar com ADAGRO | Os raios partem do ponto da granja ou do limite da propriedade? |
| Vigilância ativa para infecções que começam depois da entrada na zona | Prazo contado do início da infecção | Contar só da entrada na zona faria qualquer infecção posterior ser detectada no dia seguinte (vigilância perfeita). | a confirmar com ADAGRO | A vigilância ativa é contínua enquanto a zona estiver ativa? Com que periodicidade? |
| Saída para abate da área de vigilância | Só o perifoco bloqueia granjas notificadas | O Quadro 2 também exige exame clínico na vigilância, mas granja notificada fora do perifoco é rara (vira foco em `confirmation_days`). | a confirmar com ADAGRO | Na área de vigilância, a saída para abate também é negada a lotes com sinais clínicos? |
| Veículo exclusivo | Paradas de fora ficam para o dia seguinte | O POP diz "sempre que possível"; não há frota alternativa no modelo. | a confirmar com ADAGRO | Na prática, os veículos que atendem a área de emergência são exclusivos dela? |
| Foco ainda em confirmação quando a infecção termina | Ainda é confirmado | As amostras foram colhidas na notificação. | a confirmar com ADAGRO | Uma granja notificada cuja infecção termina antes do resultado laboratorial ainda é tratada como foco, com zonas? |

## Efeito medido (ilustrativo, não calibrado)

Mundo de teste em grade (120 granjas sintéticas, 2 bases por frota), 10 sementes ×
365 dias. Médias por semente, exceto as introduções, somadas nas 10 sementes:

| Cenário | Granjas já infectadas | Aves mortas | Aves sacrificadas | Aves perdidas (mortas + sacrificadas) | Custo (sacrifício + quarentena) | Focos | Introduções (vizinha / caminhão / aves silvestres), total nas 10 sementes |
|---|---|---|---|---|---|---|---|
| `surto_sustentado` | 72,0 | 323.638 | 0 | 323.638 | R$ 2,29 mi (0 + 2,29 mi) | 0 | 1.808 / 746 / 77 |
| `protocolo_mapa` | 25,4 | 4.855 | 120.610 | 125.465 | R$ 0,97 mi (0,96 mi + 0,01 mi) | 25,2 | 181 / 98 / 76 |

O custo só inclui sacrifício e quarentena: o modelo não precifica a interrupção do
trânsito, as entregas perdidas, o veículo exclusivo, a desinfecção nem as visitas
de vigilância, então a economia do protocolo está superestimada.

Com a mesma semente, os cenários divergem logo no início, então a comparação vale
pela média e não semente a semente. Sem despovoamento (`auto_depopulation = 0`) a
média foi de 32,4 granjas; só com o despovoamento (sem interrupção, desinfecção
nem vigilância ativa), de 44,8.
