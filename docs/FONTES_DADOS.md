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

A frota de ovos não tem mecanismo de fômite próprio: bandejas e caixas que
voltam contaminadas pelo entreposto são o caminho já modelado de troca de
carga entre caminhão e base (`hub_transfer_fraction`, lavagem na base).

### Regras do modelo

- **Lote**: cada coleta de um caminhão de abatedouro ("aves") ou de
  entreposto ("ovos") numa granja gera um lote com dia, granja, base de
  destino, caminhão, quantidade e o estado da granja *antes* da visita:
  `hidden` (infectada sem notificação: **lote exposto**), `notified` ou
  `healthy`. Granjas em quarentena ou abatidas não são visitadas.
- **Ovos**: dúzias postas desde a última coleta de ovos da granja, com o
  plantel vivo no dia da coleta (S + E + I + R) × `eggs_per_hen_day` ÷ 12.
  A queda de postura em aves doentes não é modelada. Ovos postos durante a
  quarentena saem na primeira coleta após a liberação (o descarte não é
  representado).
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
