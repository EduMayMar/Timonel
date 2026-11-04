# Referencia de configuración

Generado desde `src/timonel/schema/config.schema.yaml` con `scripts/gen_docs.py`; no lo edites a mano. Los valores se combinan en este orden: valores por defecto, tu archivo de usuario, el `.timonel.yaml` de un proyecto de confianza, las variables `TIMONEL_SECCION__CLAVE` y `--set clave=valor`. `timonel config explain <clave>` muestra de dónde sale cada valor.

## General

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `version` | `1` | `1` | Versión del esquema. |
| `mode` | `simple` \| `expert` | `simple` | El modo simple oculta opciones avanzadas en la ayuda y en la interfaz web; el experto lo muestra todo. |
| `language` | `auto` \| `en` \| `es` \| `pt` | `auto` | Idioma de los mensajes. auto usa TIMONEL_LANG y después el idioma del sistema. |

## Enrutado

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `default_profile` | string | `balanced` | Perfil usado cuando un comando no indica --profile. |
| `tiers` | object | — | Escalera ordenada de peldaños para cada nivel, de 1 (trivial) a 4 (arquitectura). |
| `profiles` | object | — | Perfiles de enrutamiento con nombre. |
| `routing.prefer_subscription` | boolean | `true` | Dentro de un nivel, probar los agentes de suscripción antes que las APIs de pago por uso. |
| `routing.deny_agents` | list of string | `[]` | Agentes que nunca se usan. |
| `routing.deny_models` | list of string | `[]` | Modelos que nunca se usan, como modelo o agente:modelo. |
| `routing.rules` | list of object | `[]` | Reglas en orden; se aplican al plan todas las que coinciden. *(avanzada)* |
| `planner.ladder` | list of object | — | Agentes o proveedores que se prueban en orden para dividir tareas. *(avanzada)* |
| `planner.prompt_template` | string | — | Prompt del planificador. Debe contener {task}, {marker} y {max_subtasks}. *(avanzada)* |
| `adaptive.enabled` | boolean | `true` | Usar el registro para reordenar peldaños. |
| `adaptive.min_samples` | integer | `5` | Intentos necesarios antes de que una ruta cuente. |
| `decompose.auto` | boolean | `false` | Descomponer sin --decompose a partir de min_complexity. *(avanzada)* |
| `decompose.min_complexity` | integer | `4` | Complejidad que activa la descomposición automática. *(avanzada)* |
| `decompose.max_subtasks` | integer | `6` | Máximo de subtareas aceptadas del planificador. *(avanzada)* |
| `decompose.max_parallel` | integer | `3` | Subtareas simultáneas. *(avanzada)* |

## Agentes

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `agents` | object | `{}` | Conectores de agentes y proveedores, combinados sobre el registro incluido (agents/*.yaml). |
| `rate_limit_patterns` | list of string | — | Expresiones regulares que marcan un intento fallido como límite de uso, para todos los agentes. *(avanzada)* |

## Clasificador

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `classifier.use_model` | boolean | `true` | Pedir la clasificación a un modelo. Si es false, solo se usan las heurísticas. |
| `classifier.ladder` | list of object | — | Agentes o proveedores que se prueban en orden para clasificar. |
| `classifier.retries` | integer | `0` | Intentos extra por peldaño cuando la respuesta no cumple el esquema. *(avanzada)* |
| `classifier.on_failure` | `heuristic` \| `fail` | `heuristic` | Qué hacer si ningún modelo da una clasificación válida. |
| `classifier.prompt_template` | string | — | Prompt que se envía al clasificador. Debe contener {task} y {marker}. *(avanzada)* |
| `classifier.heuristics.base_complexity.explain` | integer | `1` | Complejidad inicial de las tareas 'explain'. *(avanzada)* |
| `classifier.heuristics.base_complexity.review` | integer | `2` | Complejidad inicial de las tareas 'review'. *(avanzada)* |
| `classifier.heuristics.base_complexity.edit` | integer | `2` | Complejidad inicial de las tareas 'edit'. *(avanzada)* |
| `classifier.heuristics.base_complexity.debug` | integer | `2` | Complejidad inicial de las tareas 'debug'. *(avanzada)* |
| `classifier.heuristics.base_complexity.refactor` | integer | `3` | Complejidad inicial de las tareas 'refactor'. *(avanzada)* |
| `classifier.heuristics.base_complexity.architecture` | integer | `4` | Complejidad inicial de las tareas 'architecture'. *(avanzada)* |
| `classifier.heuristics.long_task_chars` | integer | `400` | Las descripciones más largas que esto cuentan como largas. *(avanzada)* |
| `classifier.heuristics.long_task_bonus` | integer | `1` | Complejidad sumada a descripciones largas. *(avanzada)* |
| `classifier.heuristics.many_files` | integer | `3` | Número de archivos mencionados que cuenta como muchos. *(avanzada)* |
| `classifier.heuristics.many_files_bonus` | integer | `1` | Complejidad sumada cuando se mencionan muchos archivos. *(avanzada)* |
| `classifier.heuristics.broad_bonus` | integer | `1` | Complejidad sumada por palabras de alcance amplio. *(avanzada)* |
| `classifier.heuristics.trivial_penalty` | integer | `1` | Complejidad restada por palabras de cambio trivial. *(avanzada)* |
| `classifier.heuristics.trivial_max_chars` | integer | `200` | Las palabras triviales solo cuentan en descripciones más cortas que esto. *(avanzada)* |
| `classifier.heuristics.high_risk_raises_tier` | boolean | `true` | Las tareas de alto riesgo por debajo del nivel 3 empiezan un nivel más arriba. *(avanzada)* |
| `classifier.heuristics.keywords.review` | list of string | — | Palabras que marcan una tarea 'review'. *(avanzada)* |
| `classifier.heuristics.keywords.architecture` | list of string | — | Palabras que marcan una tarea 'architecture'. *(avanzada)* |
| `classifier.heuristics.keywords.debug` | list of string | — | Palabras que marcan una tarea 'debug'. *(avanzada)* |
| `classifier.heuristics.keywords.refactor` | list of string | — | Palabras que marcan una tarea 'refactor'. *(avanzada)* |
| `classifier.heuristics.keywords.explain` | list of string | — | Palabras que marcan una tarea 'explain'. *(avanzada)* |
| `classifier.heuristics.broad_words` | list of string | — | Palabras que marcan un alcance amplio. *(avanzada)* |
| `classifier.heuristics.trivial_words` | list of string | — | Palabras que marcan un cambio trivial. *(avanzada)* |
| `classifier.heuristics.chat_words` | list of string | — | Palabras de un saludo o agradecimiento; un mensaje formado solo por ellas se responde en solo lectura sin editar archivos. *(avanzada)* |
| `classifier.heuristics.risky_words` | list of string | — | Palabras que marcan una tarea de alto riesgo. *(avanzada)* |

## Revisión cruzada

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `review.enabled` | boolean | `true` | Revisar cambios desde min_tier. |
| `review.min_tier` | integer | `3` | Nivel más bajo cuyos cambios se revisan. |
| `review.reviewers` | list of object | — | Revisores en orden; vacío usa todos los niveles desde el más alto. Se salta el agente ejecutor. |
| `review.max_reviewers_tried` | integer | `3` | Cuántos revisores se prueban antes de rendirse. *(avanzada)* |
| `review.strictness` | `lenient` \| `normal` \| `strict` | `normal` | Qué tan estricto se pide que sea el revisor. |
| `review.max_rounds` | integer | `1` | Rondas de corrección tras un rechazo. |
| `review.on_no_verdict` | `approve` \| `reject` | `approve` | Qué hacer si ningún revisor da un veredicto válido. |
| `review.diff_chars` | integer | `60000` | Caracteres máximos del diff enviados al revisor. *(avanzada)* |
| `review.prompt_template` | string | — | Prompt de los revisores. Debe contener {marker}, {task}, {diff} y {strictness}. *(avanzada)* |

## Escalado

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `escalation.max_escalations` | integer | `2` | Niveles extra que se prueban tras el inicial. |
| `escalation.on_verify_failure` | `next_tier` \| `next_rung` | `next_tier` | Adónde ir cuando falla la verificación. |
| `escalation.on_agent_failure` | `next_rung` \| `next_tier` | `next_rung` | Adónde ir cuando un agente falla, se cuelga o imprime basura. |
| `escalation.context_chars` | integer | `4000` | Caracteres del error anterior que se pasan al siguiente intento. |
| `escalation.context_attempts` | integer | `2` | Cuántos fallos anteriores se incluyen como contexto. *(avanzada)* |
| `escalation.cooldown_minutes` | integer | `30` | Descanso de un agente tras un límite de uso. |
| `escalation.answer_without_changes_max_complexity` | integer | `2` | Hasta esta complejidad, un agente que responde sin cambiar archivos se da por terminado en vez de escalar (0 escala siempre). |
| `escalation.reset_between_attempts` | boolean | `true` | Restablecer el worktree al commit base antes de cada intento. *(avanzada)* |

## Contexto de conversación

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `context.scope` | `off` \| `session` \| `repo` | `session` | off empieza cada tarea de cero, session comparte el contexto dentro de una sesión de la pantalla de inicio (o con --continue), repo lo comparte entre todas las tareas del repositorio. |
| `context.max_runs` | integer | `5` | Número máximo de tareas anteriores que se pasan al agente. |
| `context.stack` | boolean | `true` | Empezar un seguimiento desde la rama de la tarea anterior cuando sus cambios aún no están fusionados, para que el agente los vea. |
| `context.statuses` | list of object | `["succeeded", "applied", "failed"]` | Qué tareas anteriores cuentan como contexto, según su estado final. *(avanzada)* |
| `context.include` | list of object | `["task", "outcome", "changes", "summary"]` | Qué contar al agente de cada tarea anterior. *(avanzada)* |
| `context.max_chars` | integer | `6000` | Tamaño máximo de todo el bloque de contexto; se descartan primero las tareas más antiguas. *(avanzada)* |
| `context.summary_chars` | integer | `800` | Tamaño máximo del texto de resultado que se conserva de cada tarea anterior. *(avanzada)* |

## Verificación

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `verification.commands` | list of object | `[]` | Comandos que deben tener éxito |
| `verification.autodetect` | boolean | `false` | Si no hay comandos, detectar el comando de pruebas a partir de los archivos del proyecto. |
| `verification.detectors` | list of object | — | Archivos marcadores y el comando que implican *(avanzada)* |
| `verification.timeout_s` | integer | `900` | Límite de tiempo por comando. |
| `verification.success_exit_codes` | list of integer | `[0]` | Códigos de salida que cuentan como éxito. *(avanzada)* |
| `verification.env_allow` | list of string | `[]` | Variables de entorno extra que reciben los comandos de verificación. *(avanzada)* |

## Ejecución

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `execution.total_timeout_s` | integer | `1800` | Tiempo máximo por intento. |
| `execution.idle_timeout_s` | integer | `300` | Tiempo máximo sin salida. |
| `execution.orphan_grace_s` | number | `2.0` | Segundos de espera tras salir el agente antes de detener procesos sobrantes. *(avanzada)* |
| `execution.worktree_dir` | string | `` | Carpeta de los worktrees; vacío usa el directorio de datos. *(avanzada)* |
| `execution.branch_pattern` | string | `timonel/{id}` | Nombre de las ramas de ejecución; debe contener {id}. |
| `execution.base_ref` | string | `HEAD` | Commit o rama de la que parten los worktrees. |
| `execution.cleanup_on_apply` | boolean | `true` | Borrar worktree y rama tras aplicar. |
| `execution.write_kinds` | list of object | `["edit", "debug", "refactor", "architecture"]` | Tipos de tarea con permiso de escritura en un worktree; el resto corre en solo lectura. |
| `execution.agent_env_deny` | list of string | `[]` | Patrones de nombres de variables de entorno que nunca reciben los agentes. *(avanzada)* |
| `execution.max_output_chars` | integer | `4000000` | Salida guardada en memoria por intento (se conserva el final). *(avanzada)* |
| `execution.stored_output_chars` | integer | `200000` | Salida guardada en el registro por intento. *(avanzada)* |
| `execution.max_parallel_runs` | integer | `2` | Ejecuciones simultáneas desde la interfaz web o MCP. |
| `execution.race_contenders` | integer | `2` | Agentes que compiten en --race. |

## Presupuestos

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `budgets` | list of object | `[]` | Límites de gasto por agente y periodo, y qué hacer al alcanzarlos. |

## Registros y privacidad

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `privacy.log_level` | `debug` \| `info` \| `warning` \| `error` | `warning` | Nivel del archivo de log local. |
| `privacy.log_file` | string | `` | Ruta del archivo de log; vacío lo desactiva. |
| `privacy.redact_patterns` | list of string | `[]` | Expresiones regulares extra que se ocultan en logs, registro e interfaz. |
| `privacy.secret_env_names` | string | `(KEY\|TOKEN\|SECRET\|PASSWORD\|PASSWD\|CREDENTIAL\|AUTH)` | Nombres de variables de entorno cuyos valores siempre se ocultan. *(avanzada)* |
| `privacy.store_outputs` | boolean | `true` | Guardar las salidas de los agentes en el registro. |
| `privacy.retention_days` | integer | `0` | Borrar con purge las ejecuciones más antiguas que estos días; 0 conserva todo. |
| `privacy.update_check` | boolean | `false` | Permitir que doctor consulte PyPI por una versión nueva. Desactivado por defecto; nada más usa la red. |

## Interfaz

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `ui.host` | `127.0.0.1` \| `localhost` \| `::1` | `127.0.0.1` | Dirección local en la que escucha. |
| `ui.port` | integer | `0` | Puerto; 0 elige uno libre. |
| `ui.open_browser` | boolean | `true` | Abrir el navegador al iniciar. |
| `ui.theme` | `auto` \| `light` \| `dark` | `auto` | Tema de color. |
| `ui.density` | `comfortable` \| `compact` | `comfortable` | Espaciado de la interfaz. |
| `ui.tour` | boolean | `true` | Mostrar el recorrido guiado en la primera visita. |
| `ui.shortcuts.panel` | string | `p` | Tecla que abre el panel. *(avanzada)* |
| `ui.shortcuts.new` | string | `n` | Tecla que abre nueva tarea. *(avanzada)* |
| `ui.shortcuts.history` | string | `h` | Tecla que abre historial. *(avanzada)* |
| `ui.shortcuts.stats` | string | `s` | Tecla que abre estadísticas. *(avanzada)* |
| `ui.shortcuts.routes` | string | `r` | Tecla que abre rutas. *(avanzada)* |
| `ui.shortcuts.setup` | string | `i` | Tecla que abre configuración inicial. *(avanzada)* |
| `ui.shortcuts.settings` | string | `c` | Tecla que abre ajustes. *(avanzada)* |
| `ui.shortcuts.theme` | string | `t` | Tecla que abre cambio de tema. *(avanzada)* |
| `ui.shortcuts.language` | string | `l` | Tecla que abre cambio de idioma. *(avanzada)* |

## Pantalla de inicio en terminal

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `tui.enabled` | boolean | `true` | Abrir la pantalla de inicio interactiva al ejecutar timonel sin argumentos en una terminal. |
| `tui.logo` | `auto` \| `color` \| `mono` \| `ascii` \| `none` | `auto` | Estilo del logo: auto elige color, monocromo o ASCII según la terminal. |
| `tui.compact` | boolean | `false` | Cabecera de una línea en lugar del logo. |
| `tui.plain` | boolean | `false` | Texto plano sin cajas ni símbolos, para lectores de pantalla. |
| `tui.theme` | `auto` \| `dark` \| `light` | `auto` | Colores para terminales oscuras o claras. |
| `tui.show_spend` | boolean | `true` | Mostrar el gasto de hoy y el presupuesto restante. |
| `tui.check_on_start` | boolean | `true` | Comprobar los agentes en segundo plano al abrir la pantalla de inicio. |
| `tui.check_timeout_s` | integer | `20` | Segundos de espera por cada comprobación de agente antes de mostrar «sin respuesta». |
| `tui.status_cache_s` | integer | `300` | Segundos que se reutiliza una comprobación de agente antes de repetirla; 0 desactiva la caché. |
| `tui.refresh_s` | number | `1.0` | Cada cuántos segundos se redibuja la barra de estado. *(avanzada)* |
| `tui.notices` | list of string | `["first_run", "untrusted", "login", "outdated", "not_git"]` | Qué avisos accionables mostrar. |
| `tui.placeholder` | string | `` | Texto de ayuda dentro de la caja de entrada; vacío usa el predeterminado traducido. |
| `tui.history` | boolean | `true` | Recordar lo que escribes entre sesiones (nunca se guardan líneas que parezcan secretos). |
| `tui.history_size` | integer | `1000` | Número máximo de líneas recordadas. *(avanzada)* |
| `tui.confirm_run` | boolean | `true` | Pedir confirmación después de mostrar el plan de una tarea. |
| `tui.classify_with_model` | boolean | `true` | Clasificar cada tarea con el modelo clasificador barato antes de planificar, en vez de solo reglas de palabras clave. |
| `tui.clear_screen` | boolean | `true` | Limpiar la terminal al abrir la pantalla de inicio, como una aplicación nueva. |
| `tui.keys.profile` | string | `s-tab` | Cambiar de perfil (cheap, balanced, max). *(avanzada)* |
| `tui.keys.mode` | string | `f2` | Alternar entre modo simple y experto en esta sesión. *(avanzada)* |
| `tui.keys.help` | string | `f1` | Mostrar la ayuda. *(avanzada)* |
| `tui.keys.exit` | string | `c-d` | Salir de la pantalla de inicio. *(avanzada)* |

## Notificaciones

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `notifications.events` | list of object | `["succeeded", "failed"]` | Qué resultados avisan. |
| `notifications.desktop` | boolean | `false` | Notificación de escritorio. |
| `notifications.sound` | boolean | `false` | Campana de la terminal. |
| `notifications.webhook_url` | string | `` | URL HTTPS que recibe un POST JSON (sin texto de tarea ni salida, solo id, estado y tamaño del diff). |

## Hooks

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `hooks.pre_run` | list of object | `[]` | Comandos antes de cada ejecución; si uno falla, la ejecución se detiene. *(avanzada)* |
| `hooks.post_run` | list of object | `[]` | Comandos tras una ejecución correcta. *(avanzada)* |
| `hooks.on_failure` | list of object | `[]` | Comandos tras una ejecución fallida. *(avanzada)* |
| `hooks.timeout_s` | integer | `120` | Límite de tiempo de cada gancho. *(avanzada)* |

## Rutas de archivos

| Clave | Tipo | Por defecto | Significado |
|---|---|---|---|
| `paths.data_dir` | string | `` | Directorio de datos; vacío usa el de la plataforma. *(avanzada)* |
| `paths.ledger` | string | `` | Archivo del registro; vacío usa data_dir/ledger.sqlite. *(avanzada)* |
| `paths.cache_dir` | string | `` | Caché de scripts de instalación descargados; vacío usa data_dir/cache. *(avanzada)* |
