# Protocolo oficial (MAPA 2023) no SimGeoPE

Registro do que o modelo implementa a partir dos documentos oficiais, do que é
suposição e do que falta confirmar com a ADAGRO. Atualize este arquivo (e o
`source`/`assumption` do parâmetro em `src/simgeope/simulation/params.py`) quando
a resposta chegar.

## Fontes

- **POP:** MAPA, *Procedimento Operacional Padrão para Postos Fixos e Equipes
  Volantes em Emergência Zoossanitária — Parte específica: IAAP e DNC*, v1.0,
  nov/2023. Trata **apenas de trânsito**.
- **Plano:** MAPA, *Plano de Contingência para Emergências Zoossanitárias — Parte
  Específica: Influenza Aviária de Alta Patogenicidade e Doença de Newcastle*,
  v1.0, jun/2023 (páginas do PDF, conforme o índice). A *Parte Geral* (v1.0,
  jun/2023) é citada como "Plano, Parte Geral".
- **Ficha Técnica:** MAPA/DSA, *Ficha Técnica — Influenza Aviária (IA)*, última
  atualização jul/2023.
- **Planilha MAPA:** "Localização das investigações com coleta de amostras"
  (2022–2025), dados do professor (fora do git).

## O que as fontes dizem

| Fonte | Conteúdo |
|---|---|
| POP p. 2 | "Em um momento inicial, a movimentação de animais e produtos de risco ... deve ser interrompida em toda área de emergência"; depois, "suspensões gradativas". Do foco, nenhuma movimentação "exceto com finalidade de destruição". |
| POP p. 2 item 3; p. 8 §7 | Veículos limpos e desinfetados "antes e após cada carregamento, na entrada e saída dos estabelecimentos e nos perímetros das diferentes áreas de risco". |
| POP p. 2 item 4 | Veículos "de uso exclusivo da área de emergência" sempre que possível. |
| POP p. 4, Quadro 2; p. 7, Quadro 4 | Aves vivas: perifoco com ingresso "Proibido" e saída para abate "mediante exame clínico das aves". Ração: ingresso no perifoco e na vigilância sob supervisão do SVO, com desinfecção. |
| Plano §7, p. 13 | Ações "a partir da confirmação de um caso". "A área perifocal deve equivaler a 3 Km ao redor do foco, e a área de vigilância a 7 Km a partir da área perifocal, perfazendo um total de 10 Km"; "A área de proteção, estende-se por 15 km a partir dos limites da área de vigilância, perfazendo um total de 25 km". Distâncias podem ser ampliadas ou reduzidas por estudo epidemiológico. "Apenas devem ser colhidas amostras para diagnóstico de animais com sinais clínicos." |
| Plano §7.1, p. 14 | No foco: "Depopulação imediata de todas as aves"; destruição de produtos de "dois períodos de incubação"; ovos férteis dos "28 dias anteriores à confirmação". |
| Plano §7.2, p. 14 | Perifoco: "Investigação em todas as propriedades com aves ... a cada 3 dias"; medidas "mantidas até a fase de conclusão do foco". |
| Plano §7.3, p. 15 | Vigilância: investigação "a cada 7 dias"; caminhões e motoristas exclusivos para ração; medidas mantidas "até a fase de conclusão do foco". |
| Plano §7.4, p. 16 | Proteção: medidas "estabelecidas caso a caso pelo COEZOO". |
| Plano §10, p. 29 | Vazio sanitário: repovoamento só "no mínimo, 30 dias depois dos procedimentos de desinfecção" e com autorização do SVO. |
| Plano §11, p. 33 | Com sentinelas: 30 dias, depois repovoamento "com 20% da capacidade de alojamento", "controlados durante 60 dias, com inspeções semanais do SVO", depois "repovoamento total". Sem sentinelas: "no máximo, 20% da capacidade de alojamento", após no mínimo 30 dias da desinfecção, com monitoramento. |
| Plano §14, p. 38 | Foco encerrado quando adotadas as medidas e "não havendo mais evidências clínicas, laboratoriais e epidemiológicas" do agente — sem prazo fixo. |
| Plano, Parte Geral §5.4.6, p. 59 | Mesmo esquema: vazio ≥ 30 dias, sentinelas, repovoamento com 20%, 60 dias de controle. |
| Ficha Técnica p. 2 | Incubação "de algumas horas até 14 dias". Notificação imediata ao SVO de caso suspeito. |
| Ficha Técnica p. 3 | Caso suspeito de SRN: "mortalidade maior ou igual a 10% em até 72 horas em quaisquer estabelecimentos de criação de aves de produção comercial ou em um único galpão". |
| Planilha MAPA | Entre "data do atendimento inicial" e "data do diagnóstico conclusivo", nos 172 focos de IAAP com datas válidas: mediana de 4 dias, quartis 3–6 (2 registros com diagnóstico antes do atendimento excluídos). 165 dos 174 focos são de aves silvestres e só 1 de granja comercial (3 dias); os 53 casos descartados em granjas comerciais também têm mediana de 4 dias (quartis 2–12,5). |

## Como o modelo aplica

Com `protocol_enabled = 0` (padrão) nada do protocolo roda e um abate é final,
como no NetLogo. O estado das zonas é calculado ao fim de cada dia e vale para os
caminhões e para a vigilância do dia seguinte.

| Regra do modelo | Base |
|---|---|
| **Notificação (sempre, com ou sem protocolo):** a granja infectada é notificada no dia sorteado na infecção (`detection_time_max`) **ou** quando as mortes dos últimos `mortality_alert_window_days` dias (incluindo o de hoje) chegam a `mortality_alert_pct` do plantel alojado, o que vier primeiro (`DETECCAO`, com "(mortalidade ...)" no texto quando o gatilho é a mortalidade). Desliga com `mortality_notification = 0`. | Ficha Técnica p. 3. |
| Granja notificada vira **foco confirmado** `confirmation_days` depois (`FOCO_CONFIRMADO`), mesmo que a infecção acabe antes (as amostras já foram colhidas). | Planilha MAPA (atendimento → diagnóstico). |
| Com `auto_depopulation = 1`, o foco é despovoado no dia da confirmação (`DESPOVOAMENTO`): todas as aves vivas, inclusive as recuperadas; mesmo custo do sacrifício sanitário manual, **sem** a quarentena dos vizinhos em 5 km. | Plano §7.1. |
| Cada foco abre perifoco (≤ `perifocus_radius_km`), vigilância (≤ `surveillance_radius_km`) e proteção (≤ `protection_radius_km`), raios medidos em linha reta a partir da granja-foco. A zona de uma granja é a mais restritiva entre os focos ativos. | Plano §7 (3/10/25 km cumulativos); POP p. 3. |
| A área fica ativa até `zone_release_days` depois do despovoamento (ou do fim da infecção, se não houver despovoamento). | Suposição (ver tabela). |
| **Interrupção do trânsito:** por `standstill_days` dias depois da confirmação, nenhum caminhão para em granjas dentro do raio de proteção (`INTERDICAO_TRANSITO`). | POP p. 2; duração é suposição. |
| Depois, toda parada em granja do perifoco ou da vigilância é seguida de desinfecção (`zone_disinfection_prob`); cruzar o limite da proteção também desinfeta. Caminhões de abatedouro não param em granjas notificadas do perifoco. Depois de parar dentro da proteção, o caminhão não faz paradas fora dela no mesmo dia. | POP p. 2 itens 3–4, p. 4 Quadro 2, p. 7 Quadro 4, p. 8 §7. |
| **Vigilância ativa periódica:** cada granja do perifoco é inspecionada a cada `perifocus_detection_days` dias e cada granja da vigilância a cada `surveillance_detection_days`, contados da entrada na zona (primeira visita um intervalo depois), enquanto a zona estiver ativa. A visita só encontra a infecção se houver pelo menos 1 ave infecciosa (`CLINICAL_SIGNS_MIN_BIRDS`); aves só incubando passam (`VIGILANCIA_ATIVA`). | Plano §7 (amostras só de aves com sinais), §7.2, §7.3. |
| **Pós-foco** (todo abate com o protocolo ligado, automático ou manual): a granja fica vazia (`culled`, em quarentena) por `fallow_days` dias contados do abate (`VAZIO_SANITARIO`), e não antes de voltar uma confirmação laboratorial pendente; depois recebe um lote novo de `restock_pct` da capacidade, com a quarentena suspensa e o relógio de infecção zerado (`REPOVOAMENTO`, status `restocked`); é inspecionada a cada `restock_inspection_days` dias; `restock_monitoring_days` depois do repovoamento, se não estiver notificada, volta à capacidade total (`REPOVOAMENTO_TOTAL`). Se a inspeção achar infecção, vira foco de novo e o ciclo recomeça. | Plano §10, §11 (caminho sem sentinelas). |

## Suposições: respondidas e abertas

Valores usados no cenário "Protocolo oficial (MAPA)" (`protocolo_mapa`).

| Parâmetro / regra | Valor adotado | Base | Status | Pergunta à ADAGRO |
|---|---|---|---|---|
| `perifocus_radius_km` | 3 km | Plano §7, p. 13; POP p. 3 | Respondido | — |
| `surveillance_radius_km` | 10 km a partir do foco | Plano §7, p. 13: 7 km a partir do perifoco, "total de 10 Km" | Respondido | — |
| `protection_radius_km` | 25 km a partir do foco | Plano §7, p. 13: "total de 25 km" | Respondido | — |
| `auto_depopulation` | 1, no dia da confirmação | Plano §7.1, p. 14: "Depopulação imediata" | Respondido | — |
| `perifocus_detection_days` | visitas a cada 3 dias | Plano §7.2, p. 14 | Respondido | — |
| `surveillance_detection_days` | visitas a cada 7 dias | Plano §7.3, p. 15 | Respondido | — |
| Vigilância ativa contínua enquanto a zona estiver ativa | Sim, periódica | Plano §7.2–7.3: medidas mantidas "até a fase de conclusão do foco" | Respondido | — |
| `fallow_days` | 30 dias | Plano §10, p. 29 | Respondido (mínimo) | Na prática, quantos dias entre o despovoamento e o fim da limpeza e desinfecção? O modelo conta os 30 dias a partir do abate. |
| `restock_pct`, `restock_monitoring_days`, `restock_inspection_days` | 20%, 60 dias, semanal | Plano §11, p. 33 | Respondido | Em PE, o repovoamento usa aves sentinelas (mais 30 dias antes dos 20%)? |
| `mortality_alert_pct`, `mortality_alert_window_days` | 10%, 3 dias | Ficha Técnica p. 3 | Respondido | — |
| `confirmation_days` | 4 dias | Planilha MAPA: mediana atendimento → diagnóstico (quartis 3–6), quase só aves silvestres | Medido, com ressalva | Quantos dias, em média, entre a **notificação** e a confirmação laboratorial de um foco em granja comercial? |
| `standstill_days` | 7 dias | O POP fala em "momento inicial" sem duração; o Plano só condiciona saídas ao SVO. | a confirmar com ADAGRO | Quanto dura a interrupção inicial do trânsito em toda a área de emergência? |
| `zone_disinfection_prob` | 0,95 | Eficácia presumida da limpeza e desinfecção por parada. | a confirmar com ADAGRO | Há dado de eficácia da desinfecção de veículos nos postos fixos/volantes? |
| `zone_release_days` | 28 dias após o despovoamento | O Plano mantém as medidas "até a conclusão do foco" (§7.2–7.3) e encerra sem prazo fixo (§14); 28 dias = dois períodos de incubação de até 14 dias (Ficha Técnica; §7.1 usa "dois períodos de incubação"), como na OMSA. | a confirmar com ADAGRO | Na prática, quantos dias depois do despovoamento as áreas de perifoco e vigilância são suspensas? |
| `CLINICAL_SIGNS_MIN_BIRDS` (constante) | 1 ave infecciosa | O Plano só colhe amostras de aves com sinais clínicos (§7); 1 ave é o mínimo encontrável, um palpite otimista. | a confirmar com ADAGRO | Numa visita de vigilância, a partir de quantas aves doentes/mortas o veterinário costuma identificar o caso provável? |
| Primeira visita da vigilância ativa | Um intervalo depois da entrada na zona | O Plano dá a periodicidade, não a data da primeira visita. | a confirmar com ADAGRO | A primeira investigação no perifoco/vigilância é feita logo após a confirmação ou dentro do primeiro intervalo? |
| Raio contado do foco ou do perímetro da granja | Do ponto da granja-foco | A geometria das granjas não existe no modelo. | a confirmar com ADAGRO | Os raios partem do ponto da granja ou do limite da propriedade? |
| Saída para abate da área de vigilância | Só o perifoco bloqueia granjas notificadas | O Quadro 2 do POP também exige exame clínico na vigilância, mas granja notificada fora do perifoco vira foco em `confirmation_days`. | a confirmar com ADAGRO | Na área de vigilância, a saída para abate também é negada a lotes com sinais clínicos? |
| Veículo exclusivo | Paradas de fora ficam para o dia seguinte | O POP diz "sempre que possível"; o Plano §7.3 pede caminhões exclusivos para ração. Não há frota alternativa no modelo. | a confirmar com ADAGRO | Na prática, os veículos que atendem a área de emergência são exclusivos dela? |
| Foco ainda em confirmação quando a infecção termina | Ainda é confirmado | As amostras foram colhidas na notificação. | a confirmar com ADAGRO | Uma granja notificada cuja infecção termina antes do resultado laboratorial ainda é tratada como foco, com zonas? |
| Repovoamento após sacrifício sanitário manual | Mesmo ciclo do foco | O Plano descreve o vazio e o repovoamento da unidade despovoada, sem distinguir o motivo. | a confirmar com ADAGRO | Uma granja sacrificada preventivamente (sem foco confirmado) segue o mesmo vazio e repovoamento a 20%? |

## Efeito medido (ilustrativo, não calibrado)

Mundo de Pernambuco (`data/world/pe_environment_v1`, malha viária e hidrografia
reais) com a população sintética ancorada em dados oficiais
(`docs/FONTES_DADOS.md`: 267 granjas de cerca de 215 a 256 mil aves, 2 bases por
frota). Sementes 1–20 × 120 dias a partir de janeiro, com a taxa migratória
padrão; médias por semente. Com a mesma semente os cenários divergem logo no
início, então a comparação vale pela média e não semente a semente.

| Cenário | Granjas já infectadas | Ativas no dia 120 (sementes com ≥ 5) | Notificações (por mortalidade) | Focos | Último dia com infecção | Aves mortas | Aves sacrificadas | Repovoamentos (parcial / total) |
|---|---|---|---|---|---|---|---|---|
| `surto_sustentado` | 48,0 | 38,4 (17/20) | 43,4 (0) | 0 | 120 (ativo no fim) | 6.700.085 | 0 | — |
| `protocolo_mapa` | 3,5 | 0,1 (0/20) | 3,5 (0) | 3,4 | 39,8 | 4.354 | 836.498 | 2,4 / 1,8 |

A regra de mortalidade não mudou nenhuma dessas rodadas: com
`mortality_notification = 0` os resultados são idênticos. Pelo SEIR do modelo,
numa granja de 215 a 256 mil aves os 10% de mortes em 72 h só chegam 27 a 34
dias depois da introdução (15 dias com as 5 mil aves de antes), depois do dia de
detecção sorteado (até 21 dias); com o protocolo, a vigilância ativa acha as
granjas antes. A regra só pesa com uma detecção sorteada mais lenta
(`detection_time_max` acima de cerca de 27). No
`protocolo_mapa`, em 9 das 20 sementes o surto não passa da granja inicial.

O custo só inclui sacrifício e quarentena: o modelo não precifica a interrupção do
trânsito, as entregas perdidas, o veículo exclusivo, a desinfecção, as visitas de
vigilância nem o tempo de vazio sanitário, então a economia do protocolo está
superestimada.
