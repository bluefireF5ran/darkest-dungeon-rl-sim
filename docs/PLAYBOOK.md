# PLAYBOOK.md — cómo se juega bien a Darkest Dungeon

Criterio de un jugador experto (Fran), recogido en entrevista el 2026-08-14. Es la
**especificación de decisión** de este proyecto: hasta ahora la única que existía era
"que suba el SUCCESS", que no dice nada sobre si el bot juega bien.

## Precedencia

| Fuente | Manda sobre |
|---|---|
| Ficheros del juego (`GAME_DIR`) | Todo lo **numérico**: daño, resistencias, probabilidades, umbrales. |
| Este fichero | Todo lo **decisional**: qué hacer con esos números, y en qué orden. |
| `WIKI_MECHANICS.md` | Mecánicas que ninguno de los dos cubre. |

Este fichero no contiene datos del juego y no debe usarse para derivarlos. Donde una
regla de aquí choque con un fichero del juego, gana el fichero del juego y la regla se
corrige.

## Cómo leerlo

- **[DURA]** — restricción. El bot no debe poder violarla; va como máscara o poda.
- **[CRITERIO]** — preferencia. El bot debe *ganársela* con números (rollout), no
  tenerla cableada. Si el rollout dice otra cosa de forma consistente, es una hipótesis
  que hay que investigar, no una regla que hay que imponer.
- **[FALSA]** — cómo se comprueba que el simulador o la política cumplen la regla.

---

## 0. Función objetivo

El principio que ordena todo lo demás: **prevenir es mucho más barato que pagar.**

- **[CRITERIO] Cuando varias misiones son viables y ninguna tiene premio interesante,
  se elige la más fácil, no la más rentable.** Pasar la mazmorra con holgura evita
  estrés y enfermedades, que es donde se va el dinero de verdad.
- **[CRITERIO] Un héroe de nivel 0 es casi desechable.** Contratar es gratis. El valor
  está en el nivel y en los rasgos, no en el individuo.
- **[DURA] Los rasgos malos se arreglan echando al héroe, no curándolo.** Curar rasgos
  sólo compensa en héroes de nivel alto, y preferiblemente gratis vía curio (ver 2.4).
- **[CRITERIO] Las enfermedades sí se curan.** Son mucho más baratas que los rasgos y
  ninguna es lo bastante grave como para echar a nadie por ella.
- **[CRITERIO] El estrés se gestiona rotando composiciones, no pagando.** Baja solo cada
  semana a quien no salió de expedición. La abadía cuesta oro **y** la semana del héroe;
  usarla es aceptar quedarte sin él.
- **[DURA] La taberna no se usa para estrés. Si se usa, nunca con juego de azar** — se
  apuestan tus abalorios. Se prefieren dos semanas seguidas de abadía.


### 0.1 La jerarquía de valor, y por qué el ruler no la respeta

Añadido el **2026-09-02**, con las palabras de Fran, porque es la definición de
"jugar bien" contra la que se mide todo lo demás:

1. **[DURA] Terminar sin muertes.** *"an end game hero means many resources
   spent, so the value of the party alive and with the resources of the dungeon
   outvalues the stress of the party"*. Una muerte no se compensa con nada de lo
   que traigas.
2. **[CRITERIO] Estrés bajo.** *"stress carries outside the dungeon, making the
   hero unable of playing dungeons back to back as opposed to HP"*. El estrés es
   la restricción real de una campaña: no limita esta mazmorra, limita la
   siguiente.
3. **[CRITERIO] La vida importa MENOS.** *"hp doesnt carry outside the dungeon,
   the heroes enter full HP, hp matters only to not lose heroes, but **if you
   outvalue hp to stress its a problem as the afflictions will defeat you in the
   end**"*. La vida es instrumental: sirve para (1), no es un fin.

Todo lo demás es instrumental para esos tres. *"any kind of damage prevents
enemy stress/damage if they are defeated, same for stuns, but stuns only work
for 1 turn, in exchange, they always go net positive in action economy, as they
usually deal extra damage, hit 2 targets, debuffs as well or others"*. El daño y
el control no son objetivos: son las dos maneras de que el enemigo no actúe, y
el aturdimiento se paga solo porque casi siempre viene acompañado.

**[FALSA] El ruler actual contradice el punto 3.** `eval_expedition._run_quality`
pondera **mitad vida, mitad estrés**, y `tools/comp_ga.py` ya usa 2:1 a favor del
estrés desde el 2026-08-26 — es decir, el buscador y la evaluación llevan meses
midiendo cosas distintas, y esta jerarquía dice que el buscador es el que más se
acerca. Cambiar el peso por defecto vuelve a puntuar **todo** lo medido hasta
hoy, así que es un ruler nuevo y no un arreglo: la columna `(1:2)` de cada línea
de banda muestra el reponderado sin volver a jugar nada, y la decisión de
cambiar el defecto es de Fran.

**[FALSA] Y el ruler no ve las aflicciones en absoluto.** `_run_quality` es vida
y estrés; una partida que vuelve entera pero afligida puntúa igual que una que
vuelve serena con el mismo número de estrés. Fran sitúa a un héroe afligido
*"almost the same as a death"*, que es justo el resultado al que el ruler pone
más peso — ver `CHANGELOG.md`, "AN AFFLICTION COSTS 0.15 IN THE REWARD AND
NOTHING IN THE RULER".


---

## 1. Semana y embarque

### 1.1 El week-skip (abandonar DD1)

Jugada del early game que no existe en ninguna capa del proyecto. Mandas **un** héroe de
nivel bajo o con rasgos malos a la primera misión del Darkest Dungeon, entras, dejas
terminar al narrador y abandonas.

- **No muere nadie.** El héroe acaba prácticamente afligido y ocupa un slot del roster;
  simplemente no vuelves a elegirlo. Puede reutilizarse varias semanas seguidas.
- DD y el Patio (Courtyard) tienen misiones **encadenadas**: mientras no te pases la
  primera, siempre puedes repetirla.
- Lo que compras con la semana: rerollear las 4 misiones (zonas, dificultades y
  **abalorios de recompensa**), rerollear la diligencia (clases nuevas), abadía para los
  demás, y llegar antes al desbloqueo del gremio (semana 2-3) para bola de nieve.
- Caso canónico: tras el tutorial tienes crusader + highwayman + plague doctor + vestal.
  Con el crusader de salida (malos ataques, cleptomanía) se prefiere subir la rama
  superior de la diligencia y hacer week-skip con él antes que conformarse.
- **[CRITERIO] Fuera del early game prácticamente no se hace**: siempre hay algo
  rentable que hacer con los recursos que ya tienes.

### 1.2 Elección de misión — tres criterios, en este orden

**1. Dificultad — [DURA].** Nunca se llevan héroes fuera de su horquilla. Sin cuatro
héroes de nivel 3 no se hacen misiones veteranas; sin nivel 5, no campeón. El juego
impone además el sentido inverso: a partir de resolución 3 no se puede entrar en novato.
Tener cuatro elegibles **no basta** si no sale una composición decente.
*Única excepción:* el week-skip a DD con un nivel 0-1.

**2. Recompensas — [CRITERIO].** Las reliquias no deciden (cada región tiende a la suya).
Deciden los **abalorios**. Las misiones específicas (jefes, DD, Patio, Granja,
Chillón, misiones de la aldea) son las que garantizan calidad trofeo / muy raro /
Corte Carmesí / cristalino, y no se repiten a menudo.

> **[DURA] Regla de valor de un abalorio: vale si alguna de nuestras composiciones lo
> equipa.** Las 131 comps de `COMPS/` están completamente equipadas; un abalorio muy
> raro que no aparece en ninguna es indiferente por muy raro que sea. **Un duplicado
> tampoco vale**: son muy pocos los abalorios que una comp lleva por duplicado.

> **[DURA] Presupuesto de paciencia: 3-5 semanas.** Si en esa ventana no aparece nada
> interesante, se va igual. Hay que seguir avanzando.

**3. Composición — [CRITERIO].** Desempate: la que menos recursos cueste. Comp adecuada,
héroes con poco estrés, reliquias que hagan falta.

**[CRITERIO]** Se evitan las misiones con *A Thing from the Stars* rondando: se parece
más a una misión específica que a una genérica.

**Genéricas** (cleanse, explore, gather, activate en Ruins / Weald / Cove / Warrens): sin
preparación especial. **Específicas**: exigen equipo, abalarios, composición, nivel
maximizado para la dificultad, y a poder ser algún rasgo bueno bloqueado.

### 1.3 Composición

- **[DURA] Ningún rol es obligatorio.** No hay que enmascarar comps sin curación ni sin
  control de estrés: pagan con más disrupción o daño más rápido.
- **[CRITERIO] La mejor cura es prevenir el daño; el juego proactivo está muy
  beneficiado.** Ésta es la afirmación central sobre cómo debe jugarse el combate.
  **[FALSA]** Si la política cura de forma reactiva, juega el antipatrón. Medir con
  `tools/analyze_behaviour.py` (su hallazgo histórico: "el Cruzado cura en el 73% de sus
  turnos").
- **[DURA] El orden de formación importa mucho.** Algunos héroes toleran bailar (tienen
  habilidades de reposición); otros son piedras que necesitan su rango. El orden se
  introduce de atrás a delante (4-3-2-1) y el botón de reordenar al final de cada
  combate lo restaura.
- **[CRITERIO] La acción de Mover casi no se usa.** Reposicionar es un efecto secundario
  de habilidades ofensivas; mover por mover es una jugada reactiva.

### 1.4 Abalorios y nivel

- **[DURA] Cada expedición se quitan todos los abalorios y se reasignan** a la
  composición que va a salir; si no están los óptimos, los mejores disponibles.
  Vaciar primero evita perder de vista cuál es el mejor y si hay uno mejor en otro héroe.
  *Consecuencia:* el asignador de abalorios trabaja sobre el **inventario del baúl**, no
  sobre lo que cada héroe lleva puesto ahora.
- **[CRITERIO] Se lleva el nivel más alto que permita el tope.** Nivel 1-2 en una misión
  de novato es una ventaja fuerte sobre nivel 0.
- **[CRITERIO] Forzar el tope de resolución sólo para jefes**, para asegurar la victoria.
- **[CRITERIO] Se acepta el riesgo por un abalorio**: con tres héroes de nivel 2 y uno de
  0-1, si hay un abalorio interesante se entra, asumiendo que el flojo puede morir.

### 1.5 Provisiones — datos reales, **por zona y longitud**

El simulador usa una cantidad de convención por longitud y nada más. Esto es lo real
(`provisions/provisions_guide.jpg`). Nótese Weald contra Ruins en antídoto y Warrens en
antorchas: la variación por zona es grande.

| Ruins | Comida | Pala | Antídoto | Vendaje | Hierbas | Llave | Agua bendita | Antorcha |
|---|---|---|---|---|---|---|---|---|
| Corta | 8-12 | 2 | 0-1 | 0-1 | 0-1 | 2-3 | 2-3 | 8 |
| Media | 16-20 | 3 | 0-2 | 0-2 | 0-2 | 2-4 | 3-4 | 10-13 |
| Larga | 20-24 | 3-4 | 0-3 | 0-3 | 0-3 | 3-5 | 3-5 | 12-16 |

| Warrens | Comida | Pala | Antídoto | Vendaje | Hierbas | Llave | Agua bendita | Antorcha |
|---|---|---|---|---|---|---|---|---|
| Corta | 8-12 | 1-2 | 0-1 | 1-3 | 2-3 | 1-2 | 2-3 | 8-10 |
| Media | 12-18 | 2-3 | 0-2 | 2-3 | 3-4 | 2-3 | 3 | 10-16 |
| Larga | 16-20 | 2-4 | 0-3 | 3-4 | 3-5 | 2-4 | 3-4 | 16-20 |

| Weald | Comida | Pala | Antídoto | Vendaje | Hierbas | Llave | Agua bendita | Antorcha |
|---|---|---|---|---|---|---|---|---|
| Corta | 8-12 | 3-4 | 2-3 | 2 | 1 | 1-2 | 1 | 8 |
| Media | 16-20 | 4-5 | 3-4 | 3 | 2 | 2-3 | 1-2 | 12 |
| Larga | 20-24 | 4-6 | 4-5 | 4 | 2 | 2-4 | 2 | 16 |

| Cove | Comida | Pala | Antídoto | Vendaje | Hierbas | Llave | Agua bendita | Antorcha |
|---|---|---|---|---|---|---|---|---|
| Corta | 8-12 | 3-4 | 0-1 | 2 | 2 | 1-2 | 0-1 | 8 |
| Media | 16-20 | 4-5 | 0-2 | 3-4 | 3-4 | 2-3 | 0-2 | 12 |
| Larga | 20-24 | 4-6 | 0-3 | 4-6 | 2-4 | 2-4 | 0-2 | 16 |

**[DURA] Siempre se reserva dinero para las provisiones** antes de gastar en mejoras.

---

## 2. Mazmorra

### 2.1 Objetivo y botín — la métrica actual no mide esto

**El objetivo de la misión no es el final de la run.** Antes de cerrar se evalúan dos
cosas:

- **¿Hay scouting posible?** Sobre todo por acampada. Explorar da reducción de estrés
  y descubre tesoros y salas secretas.
- **¿Hay tesoros ya explorados?** Tesoro en la sala del último combate, sala secreta no
  recogida, cofre visto en el scout, altar del Shambler. **[CRITERIO]** Se van a buscar:
  suele costar un combate adicional y la recompensa es muy alta.

**[CRITERIO] Cuidado con los jefes errantes** (Shambler, Collector, Thing from the
Stars): no equivalen a un combate normal. Se evalúa el estrés de la party, y si no se
está preparado se **abandona la expedición**.

> **[FALSA] Nuestra métrica de éxito es "completar todos los combates y cero muertes".
> Eso no es lo que se juega.** El objetivo real es cumplir la misión más el contenido
> secundario rentable, con el estrés como precio.

### 2.2 Ruta

- **[CRITERIO] Maximizar zona nueva descubierta** (más oportunidades de scout) y
  **minimizar pasar dos veces por el mismo sitio**. Es un problema de búsqueda de
  caminos sobre `persist.map.json`, no de aprendizaje.
- **[CRITERIO] Si se puede, ir directo al objetivo.** Crítico en misiones específicas:
  llegar al jefe con el mejor HP y estrés posibles.

> **Implementado en `bridge/route.py` (2026-08-14).** El grafo sale de
> `bridge/dungeon_map.py`; la posición de la party y la misión activa salen del save de
> raid (`in_area` y `raid_instance`, ambos recuperados al hacer esto). El objetivo viene
> escrito en texto plano en `goal_ids` — `explore_all_rooms`, `battle_all_rooms` — así que
> no hay que adivinarlo. El orden de visita es un recubrimiento **exacto** de coste mínimo.
>
> Dos matices que salieron de los datos:
>
> - **Tu "minimizar pasar dos veces" tiene un "si puedo" que pesa.** Los mapas reales se
>   ramifican, y una sala en fondo de saco hay que desandarla: en una captura hacen falta
>   2 repeticiones para llegar a `rooC`. Un primer test que exigía cero repeticiones falló
>   justo ahí. El planificador reporta el retroceso en vez de fingir una ruta que no existe.
> - **Los desvíos se tasan, no se deciden.** Tu 2.1 los valora en "como mucho un combate
>   adicional y un rewards muy alto", pero cuánto vale el botín frente a movimientos y
>   peleas no está en ningún fichero del juego. Cada desvío sale como `+N movimientos,
>   +N combates` y la decisión es tuya. En misión de jefe, explorar pasa a ser desvío.
>
> Ejemplo real (misión de jefe, warrens): la ruta al jefe son 3 movimientos y 1 combate;
> `rooG guarded_treasure(locked_strongbox) +1 movimiento, +1 combate`.

> **ACTUALIZADO 2026-09-06: los desvíos ya se DECIDEN, con una función objetivo
> explícita.** Lo de arriba ("los desvíos se tasan, no se deciden") era cierto
> mientras no hubiera modelo de botín. `data/loot_loader.py` lee
> `loot/darkness_overrides.loot.json`, las 58 tablas de `loot/*.json` y los
> `sell_gold_value` del inventario, así que un desvío ya se puede valorar en oro
> real en vez de en movimientos.
>
> Tu objetivo, con tus palabras: *"minimizing low torch battles + maximizing
> loot at the same time"*. Se implementa **en dos niveles, sin inventar ningún
> peso**: suelo duro de antorcha (76) para todo combate planificado que la
> mochila pueda pagar, y entre los planes que lo respetan, el de más botín
> esperado. `bridge/lootplan.py`.
>
> Lo que sale de ahí y no estaba en 2.2: **aplazar un curio y recogerlo a la
> vuelta sale gratis**, porque hay que volver a la entrada de todas formas para
> entregar la misión — y a antorcha 0 vale unas tres veces más. Lo que NO sale
> gratis es un desvío que solo existe para volver, porque
> `corridor_return_content` repuebla un pasillo ya limpiado (+50% de
> probabilidad a oscuras).

### 2.3 Antorcha — el apagón

- **[CRITERIO] Mantenerse en 75+ (luz radiante)** por defecto.
- **[DURA] Apagón:** en el **último turno de un combate, antes del ataque que remata**,
  bajar la antorcha a 0 (shift+click) para maximizar el botín de ese combate.
- **[DURA] Coste del apagón: 4 antorchas** para volver a 100. Si la recompensa del
  combate no lo cubre, no se hace.
- **[DURA] En el último combate de la mazmorra siempre compensa.** Y es especialmente
  relevante en misiones específicas, para que el jefe suelte más botín.

> **Implementado en `bridge/torchlight.py` (2026-08-14), y yo me equivocaba: el modelo de
> botín estaba en `rules.json` desde el principio.** Dije dos veces que esto no se podía
> representar por falta de modelo de botín. `darkness.range_table` trae cinco tramos y
> tasa la oscuridad en las dos direcciones a la vez:
>
> | antorcha | oro | tiradas de tesoro | mstr atk/dmg/crit | estrés | scouting | sorpresa |
> |---|---|---|---|---|---|---|
> | 76-100 | — | — | — | — | +15 | monstruos +25 |
> | 51-75 | — | — | 0/0/+1 | +5/+10 | +7.5 | monstruos +15 |
> | 26-50 | +10% | +4 | +5/+10/+2 | +10/+20 | — | héroes +15 |
> | 1-25 | +15% | +8 | +10/+15/+3 | +25/+30 | — | héroes +25 |
> | 0 o menos | **+30%** | **+12** | +12.5/+25/+5 | +35/+40 | — | héroes +40 |
>
> El cargador se estaba **tirando** las columnas de botín. Ahora se leen (el motor sigue
> sin aplicarlas, así que el combate no cambia).
>
> **Corrección a tu regla del 75: 75 es *dim*, no radiante.** `darkness.lowest_excluded`
> es `true`, así que los tramos son 76-100 / 51-75 / 26-50 / 0-25. El motor lo tenía al
> revés — metía el 75 en el tramo radiante mientras su propio comentario citaba la lista
> correcta. Inerte para el entrenamiento (siempre juega a 100) pero **no** para el
> puente, que le pasa tu antorcha real: parcando en 75 exacto, el asesor simulaba un
> combate radiante mientras el juego corría uno dim. Arreglado. **Si quieres el tramo
> bueno, es 76.**
>
> **Un caso gratis que no mencionaste:** `camp_restore_torch` es **100**. Acampar rellena
> la antorcha, así que un apagón en el combate justo antes de una hoguera cuesta cero.
> (Y `ambush_torch_reduction` es -100: una emboscada en el campamento te la pone a 0.)
>
> **El coste es marginal, no absoluto.** Lo que cuesta el apagón es volver a donde
> estabas, no a 100: con la antorcha ya en 25 es **1** antorcha, no 4. Tus "4 antorchas"
> son correctas partiendo de 100, que es como juegas.
>
> **Lo único que no es dato del juego** es cuánto restaura una antorcha: no está en el
> fichero de inventario, ni en los efectos, ni en `raid/`. Uso tu medida (25) y está
> etiquetada como tal en la constante.

### 2.4 Curios — política completa y determinista

| Curio | Acción |
|---|---|
| Libros y bibliotecas | **No tocar.** Netamente negativos. |
| Cualquiera con provisión que da recompensa | **Interactuar siempre** con su provisión. |
| Montones de piedras | Pala, si queda. |
| Reducción de estrés (agua bendita) | Al héroe con **más estrés o menos HP**. |
| Buff de daño | Al héroe con **más daño puro esperado**. |
| Quitar rasgos | Al que tenga **el peor rasgo de la composición**. Merece la pena jugársela: repitiendo casi siempre se acaba quitando. Muy útil en Cove con héroes de nivel alto y malos rasgos. |
| Altar del Shambler | Interactuar sólo si te ves capaz, y **poniendo antes la peor formación posible** — el shuffle es forzoso, así que cualquier alternativa la mejora. |
| Trampas | Al héroe con **más porcentaje de desarme**, clicando para desactivarla. |

> **Implementado en `bridge/curios.py` (2026-08-14), y las tablas del juego corrigen la
> regla en tres puntos.** La política no cablea nada de lo de arriba: puntúa cada
> desenlace desde `curios/curio_type_library.csv` resolviendo cada resultado concreto
> contra las definiciones reales de efectos y manías. Así tu regla es una **predicción
> que el módulo puede fallar**, no un supuesto. Lo que salió:
>
> - **Los libros: confirmado sólo a medias.** `stack_of_books` sale en **−0.11**
>   (su único botín es `JOURNALONLY`, una página de diario), pero `bookshelf` sale
>   **+0.07** y `pile_of_scrolls` **+0.19**, los dos porque pueden dar *scouting*. Los
>   tres están pegados a cero, así que "no merece el turno" se sostiene; "netamente
>   negativo" no. Zanjarlo necesita **magnitudes** — cuánto pesa un Stress 3 frente a una
>   tirada de botín — y ningún fichero del juego las da. Inventarlas sería una convención
>   disfrazada de dato. **Queda a tu criterio.**
> - **Una provisión que encaja no siempre es buena.** 4 de las 46 interacciones con ítem
>   te perjudican: `stack_of_books` y `bas_relief` responden con **Stress 5**,
>   `decorative_urn` responde a la pala con la manía negativa `guilty_conscience`, y el
>   altar del Shambler responde a la antorcha con el jefe. `bas_relief` es además el
>   contraejemplo de "cava siempre que puedas".
> - **El tipo de resultado no decide la polaridad.** `Effect` es hostil en
>   `stack_of_books` y beneficioso en `confession_booth` (cura estrés). Hay que mirar el
>   resultado concreto.
>
> Y un matiz que cambia la regla de "no tocar": **todas las manías con `curio_tag` son
> negativas** — 20 compulsiones, del 20% al 60% — así que saltarse un curio es un consejo,
> no una garantía. La herramienta nombra al héroe que puede tocarlo igualmente.
>
> **Las trampas quedan fuera**: el desarme es la resistencia `resistance`/`trap`, que sale
> de clase + manías + abalorios, y ni `CombatantState` ni `live_state.py` la llevan.
> Ordenar héroes por ella sería inventado.

### 2.5 Campamento

- **[CRITERIO] A mitad de la run**, y sobre todo para gestionar el estrés ya acumulado.
- **[CRITERIO] Si el estrés va bien, se fuerza más y se acampa al final**, como
  pseudo-abadía, para dejar la composición lista para encadenar otra mazmorra.

> **Implementado en `bridge/camp.py` (2026-08-14).** El *reparto* de los 12 puntos ya lo
> resolvía `engine/camping.py`; se reutiliza tal cual. Lo nuevo es **cuándo** quemar la
> hoguera, y son dos puertas, ninguna inventada: que la hoguera devuelva HP o estrés de
> verdad (los buffs no cuentan, y cero no necesita umbral), y que toque según
> `_camp_schedule`, el reparto uniforme de hogueras que ya usaba el simulador. Con
> `FIREWOOD_PER_BAND` (0 corta / 1 media / 2 larga, dato real) y los combates ya peleados
> del save, tu "a mitad de la run" sale solo en una misión media. La pseudo-abadía es la
> excepción: última leña y run casi acabada, se acampa igual.
>
> **Dos reglas anteriores parecían principiadas y eran falsas; las cazaron los tests.**
> (1) Comparar el estrés acumulado contra el que una hoguera puede quitar: ese techo se
> medía con la vida llena, lo que empuja todo el presupuesto a estrés, mientras que el
> gasto real también compra HP y buffs — en una captura tuya se llevaban un factor de
> cuatro. (2) Mirar si se gastan los 12 puntos: **una party totalmente sana los gasta
> igual**, en buffs. No mide nada.
>
> **El riesgo de emboscada (33%) se reporta, no se tasa** — cambiarlo por recuperación es
> criterio tuyo, no hay fichero que lo diga.
>
> Y una nota que pediste implícitamente en 1.3: `skills_used` registra **quién ejecuta**,
> no a quién cura. Con dos Highwaymen en la party, `first_aid` sale dos veces y es legal:
> uno por héroe.

### 2.6 Retirada — se paga en moneda de pueblo, no en probabilidad

Es **rara**, y son situaciones límite. Dos razones:

1. **Shuffle catastrófico.** Retirarse y volver a entrar: se comen 20 de estrés cada uno
   pero se recupera la formación. A veces es mejor que seguir con la comp injugable.
2. **Alguien va a morir, o lleva rato afligido**, y reparar el estrés de los cuatro
   cuesta más que reparar el de uno solo más perder la oportunidad de esa mazmorra.

> **Replanteado en `bridge/retreat.py` (2026-08-14).** El aviso decía
> "P(éxito) < 50% → considera retirarte". No estaba mal calibrado: estaba mal planteado.
> Ahora se restan dos facturas en oro, y **todos los tipos de cambio son datos del juego**:
>
> | qué | oro | fuente |
> |---|---|---|
> | un punto de estrés | **22.2** abadía sin mejorar, **10.0** al máximo | `abbey.building.json`: 1000/850/700 contra 45/56/70 curados |
> | retirar a los cuatro | 20 × 4 = **~1.776** | `rules.json` `combat_retreat_stress` = 20 |
> | un héroe de rango 4 muerto | **~36.000** | `upgrades/heroes/<clase>.upgrades.json` |
> | una misión genérica | **3.000** oro + 3 escrituras + un abalorio | `raid_instance.completion_reward` |
>
> **Una muerte cuesta ~20 veces una retirada completa y ~12 veces la misión por la que se
> arriesga.** Ese cociente *es* tu segunda razón de 2.6, y explica la forma de tu juego que
> el marco de probabilidad no podía: te retiras poquísimo porque el estrés es barato, y
> cuando lo haces es tajante porque un héroe no lo es.
>
> Y como contratar es gratis (tu 0.1), lo que muere no es "un héroe" sino el oro metido en
> sus rangos — por eso **el signo cambia con la calidad de la party**: mismas 1.0 muertes
> esperadas, misma misión, retirada con party veterana y adelante con reclutas. Un umbral
> sobre `success_rate` no puede expresar eso. En una captura tuya el Man-at-Arms es rango
> 0/0 y lleva **0 oro** encima.
>
> Ejemplo real: `P(terminar entero) 67%, muertes esperadas 0.8` → **adelante**, porque esa
> party es barata y la misión vale más. La regla vieja coincidía, por el motivo equivocado.
>
> **Lo que no se tasa a propósito:** las reliquias (no tienen precio en oro, compran
> edificios) y el abalorio de recompensa (tu 1.2 lo valora por si alguna comp lo equipa).
> Se reportan al lado del total, no se meten dentro con un cambio inventado.
>
> Tu primera razón —**retirarse y volver a entrar para deshacer un shuffle**— está en
> `shuffle_is_worth_undoing`: corre la misma mazmorra dos veces, una por formación, así que
> la respuesta sale en muertes y estrés, no de una heurística de formación. El motor sigue
> sin poder representar la reentrada.

### 2.7 Death's Door

Sacarlo de la puerta de la muerte con curas o comida; acampar y curarlo si se puede. Si
se puede restaurar parcialmente el estado inicial, se continúa. Si no: retirarse, o
**tantear el siguiente combate y retirarse si se ve que no**.

---

## 3. Combate

**La política actual ya juega esto bien.** Validado cualitativamente: priorización de
objetivos, control y apertura son "lo que yo elegiría", y donde no coincidía era una
alternativa razonable, no un fallo. Lo que sigue son las reglas que quedan por
comprobar.

### Resultados medidos (2026-08-14)

`python tools/check_playbook.py --model logs/full_expedition/best_model.zip --episodes 36
--comps 12 --comp-seed 1` — 4366 turnos de héroe, 314 combates. **Dos reglas
incumplidas, una puerta ausente pero latente, y una hipótesis mía que era falsa.**

- **3.1 Objetivo — [CRITERIO]** Prioridad por amenaza. *Conforme* (validación
  cualitativa del jugador).
- **3.2 Curar — [CRITERIO]** Sólo en **situaciones extremas o en stall**. En el resto,
  jugar proactivo.

  > **INCUMPLIDA.** 549 curaciones, el 12.6% de todos los turnos. Puerta de la muerte
  > 4.4%, HP crítico (<34%) 9.5%, stall 35.7% — y **50.5% ninguna de las tres**. La
  > mitad de las curaciones caen sobre un héroe por encima del 34% de vida, fuera de
  > stall y sin estar en la puerta de la muerte. Es exactamente el antipatrón reactivo.

- **3.3 Economía de acciones con muerte asegurada — [CRITERIO]** En stall, si X está
  aturdido y va a morir a manos de Y o Z, y W juega antes, **W gasta su turno en curar o
  quitar estrés**: la muerte ya está garantizada y rematar le toca a otro.

  > **INCUMPLIDA.** En las 69 veces que se dio la situación (todo enemigo vivo aturdido
  > y la party necesitando recuperarse): **58.0% atacar**, 27.5% recuperarse. Se
  > amontona sobre una muerte ya asegurada. Causa probable: nada en la recompensa
  > distingue "esta muerte ya está garantizada" de "hay que rematar".

- **3.4 Alargar el combate — [CRITERIO]** Sí se hace, **sobre todo con curanderos o
  curanderos de estrés en la composición**.

  > **PUERTA AUSENTE, PERO LATENTE.** `is_recovery_phase()` tiene tres puertas —un
  > enemigo vivo, la party necesita recuperarse, presupuesto de turnos— y **ninguna mira
  > si la composición puede aprovecharlo**. En la práctica casi nunca muerde: de las 132
  > comps del pool, **4 (3%) no tienen ni curación ni cura de estrés** y 9 (7%) no curan
  > HP. Ninguna salió en la muestra de 12.
  >
  > Hallazgo lateral más interesante: en fase de recuperación el **61.5% de los turnos
  > baratos se gastan atacando** y sólo el 24.1% curando. El descuento de tempo se
  > concede y no se usa para lo que se concedió.

- **3.5 Buffs y debuffs — [CRITERIO]** Normalmente no compensan. Dos excepciones:
  - **Buffs de esquiva**: evitar un ataque evita el daño, el estrés, el DoT que traía y
    la posibilidad de crítico. Hay comps construidas sobre esto, pero sin abalorios que
    lo potencien pierde bastante.
  - **Jefes y combates largos**, donde el buff se amortiza.

  > **MI HIPÓTESIS ERA FALSA, y el problema real es otro.** Dije que no había jefes en
  > el entrenamiento: los hay. `ENEMY_CATEGORIES_FOR_TRAINING` incluye `boss`, y salen
  > en el **3.07%** de los encuentros ponderados por probabilidad — una frecuencia
  > razonable frente al juego real.
  >
  > Lo que sí falta es el **régimen**: los combates duran **mediana 2 rondas, p90 4**;
  > sólo el 14.0% pasa de 3 rondas y el 4.5% de 5. Un buff casi nunca tiene tiempo de
  > amortizarse, así que la política aprendió correctamente a no buffear *en este
  > simulador* — y eso no dice nada sobre si buffearía bien en un combate largo.
  > **Contrastado contra el juego real y la brecha es pequeña.** Contando
  > `bs_start_round` entre `bs_create` en las trazas de `archive/traces/`: 13 batallas
  > reales dan mediana **3** rondas, máximo 7. El simulador da mediana **2**. Una ronda
  > menos — y la muestra real está sesgada hacia arriba, porque incluye jefes (Hag,
  > Collector, Shambler). **La duración de combate no explica las infracciones de 3.2 y
  > 3.3**: son problemas de política, no de fidelidad. Y a 3 rondas los buffs siguen sin
  > amortizarse, así que la conclusión de 3.5 se mantiene.
  >
  > Anomalía a mirar aparte: un combate de **517 rondas** en la muestra. Es la patología
  > de estancamiento conocida, y sigue viva.
- **3.6 Control — [CRITERIO]** Se prioriza daño, pero **casi todo aturdimiento es
  netamente positivo en economía de acciones**, sobre todo si saca de la ecuación al
  objetivo más peligroso. *Conforme.*
- **3.7 Riesgo — [CRITERIO]** Se prefieren las jugadas seguras: la probabilidad
  acumulada de fallar tras muchas arriesgadas es altísima. Se toma la arriesgada cuando
  **asegura eliminar al objetivo y no hay posibilidad de perder a uno propio**.
- **3.8 Apertura — [CRITERIO]** Existe, pero es esencialmente priorización de amenazas.
  *Conforme.*

---

## 4. Pueblo

### 4.1 Orden de la semana

Primero mirar qué se puede mejorar en la aldea, **antes** de mirar las misiones nuevas.

**Prioridad de edificios:**
`distritos (si se puede) > gremio = herrería > diligencia >>>> abadía > sanatorio >> carromato nómada > superviviente >>>> taberna`

**Mejoras que de verdad importan**, en cualquier edificio:

- +nivel de arma / armadura
- +nivel de habilidad
- +número de reclutas
- +tamaño de la lista de reclutas
- +slots de rasgo / sala de penitencia
- +reclutas con experiencia

Lo demás: seguir ahorrando para un nivel más alto de éstas o para un distrito. La
reducción de coste de servicios está bien a nivel 1, pero **no merece maximizarla**.

### 4.2 Distritos

| Nivel | Distritos |
|---|---|
| **S** — cambian la partida | Granero, Campamento del Cartógrafo |
| **A** — beneficio significativo | Pozo Contaminado, Sala de Espectáculos, Sala de Entrenamiento |
| **B** — sin duda comprables | Huerto Miasmático, Altar de Luz, Ateneo, Casa de la Mano Amarilla |
| **C** — poca prioridad, no compensan | Sala de Estudios Geológicos, Banco, Viñedos Sanguinos, Hoguera de los Forasteros, Teatro de Marionetas, El Molino |
| **F** — sólo logros | Red Hook |

Banco, Viñedos Sanguinos y Teatro de Marionetas mejoran si se abusa del week-skip, pero
llegan en una etapa tan tardía que no compensan igualmente.

### 4.3 Roster

- **[CRITERIO] Dos héroes por clase**, y el máximo de clases posible, para poder montar
  composiciones distintas.
- **[CRITERIO] Tres** de las clases que aparecen en muchas comps (montero, ocultista,
  cruzado), por rotación de estrés.

### 4.4 Gremio y herrería

**[DURA] Primero se decide quién va a la misión, y se maximiza a ese cuarteto** si el
dinero llega — reservando siempre lo de las provisiones. **No se sube a todo el mundo
por poder subirlo.**

---

## 5. Meta

- **Dónde se espera que el bot gane:** cálculo y toma de decisiones dentro de un combate
  individual, y sobre todo **jefes, con modelos específicos por jefe** — cada uno tiene
  su mecánica.
- **Dónde se espera que no tenga nada que hacer:** intuiciones, gestión de inventario,
  gestión de roster, y en general decisiones donde la "mejor jugada" no está clara y
  admite interpretación. También **combates con shuffle** y **composiciones aleatorias**,
  donde está menos entrenado. *(Ambas son medibles hoy en el simulador.)*
- **Confianza:** hoy no se seguiría una recomendación en contra del instinto. **El
  destinatario de las capas fuera de combate es un jugador con menos experiencia**, no
  el autor de este playbook.

## 6. Orden de construcción

Fijado por el jugador, y sustituye a cualquier priorización previa:

1. **Todo lo de expedición**: provisiones, ruta y scouting, curios, antorcha, gestión de
   inventario en mazmorra, acampada.
2. **La gestión del pueblo va la última.** Es otro problema distinto.
