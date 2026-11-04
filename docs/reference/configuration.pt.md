# Referência de configuração

Gerado a partir de `src/timonel/schema/config.schema.yaml` por `scripts/gen_docs.py`; não edite à mão. Os valores são combinados nesta ordem: padrões embutidos, seu arquivo de usuário, o `.timonel.yaml` de um projeto confiável, variáveis `TIMONEL_SECAO__CHAVE` e `--set chave=valor`. `timonel config explain <chave>` mostra a origem de cada valor.

## Geral

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `version` | `1` | `1` | Versão do esquema. |
| `mode` | `simple` \| `expert` | `simple` | O modo simples oculta opções avançadas na ajuda e na interface web; o especialista mostra tudo. |
| `language` | `auto` \| `en` \| `es` \| `pt` | `auto` | Idioma das mensagens. auto usa TIMONEL_LANG e depois o idioma do sistema. |

## Roteamento

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `default_profile` | string | `balanced` | Perfil usado quando um comando não indica --profile. |
| `tiers` | object | — | Escada ordenada de degraus para cada nível, de 1 (trivial) a 4 (arquitetura). |
| `profiles` | object | — | Perfis de roteamento com nome. |
| `routing.prefer_subscription` | boolean | `true` | Dentro de um nível, tentar os agentes de assinatura antes das APIs pagas por uso. |
| `routing.deny_agents` | list of string | `[]` | Agentes que nunca são usados. |
| `routing.deny_models` | list of string | `[]` | Modelos que nunca são usados, como modelo ou agente:modelo. |
| `routing.rules` | list of object | `[]` | Regras em ordem; todas as que coincidem são aplicadas ao plano. *(avançada)* |
| `planner.ladder` | list of object | — | Agentes ou provedores tentados em ordem para dividir tarefas. *(avançada)* |
| `planner.prompt_template` | string | — | Prompt do planejador. Deve conter {task}, {marker} e {max_subtasks}. *(avançada)* |
| `adaptive.enabled` | boolean | `true` | Usar o registro para reordenar degraus. |
| `adaptive.min_samples` | integer | `5` | Tentativas necessárias antes que uma rota conte. |
| `decompose.auto` | boolean | `false` | Decompor sem --decompose a partir de min_complexity. *(avançada)* |
| `decompose.min_complexity` | integer | `4` | Complexidade que ativa a decomposição automática. *(avançada)* |
| `decompose.max_subtasks` | integer | `6` | Máximo de subtarefas aceitas do planejador. *(avançada)* |
| `decompose.max_parallel` | integer | `3` | Subtarefas simultâneas. *(avançada)* |

## Agentes

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `agents` | object | `{}` | Conectores de agentes e provedores, combinados sobre o registro incluído (agents/*.yaml). |
| `rate_limit_patterns` | list of string | — | Expressões regulares que marcam uma tentativa falha como limite de uso, para todos os agentes. *(avançada)* |

## Classificador

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `classifier.use_model` | boolean | `true` | Pedir a classificação a um modelo. Se for false, só as heurísticas são usadas. |
| `classifier.ladder` | list of object | — | Agentes ou provedores tentados em ordem para classificar. |
| `classifier.retries` | integer | `0` | Tentativas extras por degrau quando a resposta não segue o esquema. *(avançada)* |
| `classifier.on_failure` | `heuristic` \| `fail` | `heuristic` | O que fazer se nenhum modelo der uma classificação válida. |
| `classifier.prompt_template` | string | — | Prompt enviado ao classificador. Deve conter {task} e {marker}. *(avançada)* |
| `classifier.heuristics.base_complexity.explain` | integer | `1` | Complexidade inicial das tarefas 'explain'. *(avançada)* |
| `classifier.heuristics.base_complexity.review` | integer | `2` | Complexidade inicial das tarefas 'review'. *(avançada)* |
| `classifier.heuristics.base_complexity.edit` | integer | `2` | Complexidade inicial das tarefas 'edit'. *(avançada)* |
| `classifier.heuristics.base_complexity.debug` | integer | `2` | Complexidade inicial das tarefas 'debug'. *(avançada)* |
| `classifier.heuristics.base_complexity.refactor` | integer | `3` | Complexidade inicial das tarefas 'refactor'. *(avançada)* |
| `classifier.heuristics.base_complexity.architecture` | integer | `4` | Complexidade inicial das tarefas 'architecture'. *(avançada)* |
| `classifier.heuristics.long_task_chars` | integer | `400` | Descrições mais longas que isto contam como longas. *(avançada)* |
| `classifier.heuristics.long_task_bonus` | integer | `1` | Complexidade somada a descrições longas. *(avançada)* |
| `classifier.heuristics.many_files` | integer | `3` | Número de arquivos mencionados que conta como muitos. *(avançada)* |
| `classifier.heuristics.many_files_bonus` | integer | `1` | Complexidade somada quando muitos arquivos são mencionados. *(avançada)* |
| `classifier.heuristics.broad_bonus` | integer | `1` | Complexidade somada por palavras de escopo amplo. *(avançada)* |
| `classifier.heuristics.trivial_penalty` | integer | `1` | Complexidade subtraída por palavras de mudança trivial. *(avançada)* |
| `classifier.heuristics.trivial_max_chars` | integer | `200` | Palavras triviais só contam em descrições mais curtas que isto. *(avançada)* |
| `classifier.heuristics.high_risk_raises_tier` | boolean | `true` | Tarefas de alto risco abaixo do nível 3 começam um nível acima. *(avançada)* |
| `classifier.heuristics.keywords.review` | list of string | — | Palavras que marcam uma tarefa 'review'. *(avançada)* |
| `classifier.heuristics.keywords.architecture` | list of string | — | Palavras que marcam uma tarefa 'architecture'. *(avançada)* |
| `classifier.heuristics.keywords.debug` | list of string | — | Palavras que marcam uma tarefa 'debug'. *(avançada)* |
| `classifier.heuristics.keywords.refactor` | list of string | — | Palavras que marcam uma tarefa 'refactor'. *(avançada)* |
| `classifier.heuristics.keywords.explain` | list of string | — | Palavras que marcam uma tarefa 'explain'. *(avançada)* |
| `classifier.heuristics.broad_words` | list of string | — | Palavras que marcam um escopo amplo. *(avançada)* |
| `classifier.heuristics.trivial_words` | list of string | — | Palavras que marcam uma mudança trivial. *(avançada)* |
| `classifier.heuristics.risky_words` | list of string | — | Palavras que marcam uma tarefa de alto risco. *(avançada)* |

## Revisão cruzada

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `review.enabled` | boolean | `true` | Revisar mudanças a partir de min_tier. |
| `review.min_tier` | integer | `3` | Nível mais baixo cujas mudanças são revisadas. |
| `review.reviewers` | list of object | — | Revisores em ordem; vazio usa todos os níveis a partir do mais alto. O agente executor é pulado. |
| `review.max_reviewers_tried` | integer | `3` | Quantos revisores são tentados antes de desistir. *(avançada)* |
| `review.strictness` | `lenient` \| `normal` \| `strict` | `normal` | Quão rigoroso o revisor deve ser. |
| `review.max_rounds` | integer | `1` | Rodadas de correção após uma rejeição. |
| `review.on_no_verdict` | `approve` \| `reject` | `approve` | O que fazer se nenhum revisor der um veredito válido. |
| `review.diff_chars` | integer | `60000` | Caracteres máximos do diff enviados ao revisor. *(avançada)* |
| `review.prompt_template` | string | — | Prompt dos revisores. Deve conter {marker}, {task}, {diff} e {strictness}. *(avançada)* |

## Escalonamento

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `escalation.max_escalations` | integer | `2` | Níveis extras tentados após o inicial. |
| `escalation.on_verify_failure` | `next_tier` \| `next_rung` | `next_tier` | Para onde ir quando a verificação falha. |
| `escalation.on_agent_failure` | `next_rung` \| `next_tier` | `next_rung` | Para onde ir quando um agente falha, trava ou imprime lixo. |
| `escalation.context_chars` | integer | `4000` | Caracteres do erro anterior passados para a próxima tentativa. |
| `escalation.context_attempts` | integer | `2` | Quantas falhas anteriores são incluídas como contexto. *(avançada)* |
| `escalation.cooldown_minutes` | integer | `30` | Descanso de um agente após um limite de uso. |
| `escalation.reset_between_attempts` | boolean | `true` | Restaurar o worktree ao commit base antes de cada tentativa. *(avançada)* |

## Verificação

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `verification.commands` | list of object | `[]` | Comandos que devem ter sucesso |
| `verification.autodetect` | boolean | `false` | Se não houver comandos, detectar o comando de testes a partir dos arquivos do projeto. |
| `verification.detectors` | list of object | — | Arquivos marcadores e o comando que implicam *(avançada)* |
| `verification.timeout_s` | integer | `900` | Limite de tempo por comando. |
| `verification.success_exit_codes` | list of integer | `[0]` | Códigos de saída que contam como sucesso. *(avançada)* |
| `verification.env_allow` | list of string | `[]` | Variáveis de ambiente extras recebidas pelos comandos de verificação. *(avançada)* |

## Execução

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `execution.total_timeout_s` | integer | `1800` | Tempo máximo por tentativa. |
| `execution.idle_timeout_s` | integer | `300` | Tempo máximo sem saída. |
| `execution.orphan_grace_s` | number | `2.0` | Segundos de espera após o agente sair antes de parar processos restantes. *(avançada)* |
| `execution.worktree_dir` | string | `` | Pasta dos worktrees; vazio usa o diretório de dados. *(avançada)* |
| `execution.branch_pattern` | string | `timonel/{id}` | Nome dos branches de execução; deve conter {id}. |
| `execution.base_ref` | string | `HEAD` | Commit ou branch de onde partem os worktrees. |
| `execution.cleanup_on_apply` | boolean | `true` | Apagar worktree e branch após aplicar. |
| `execution.write_kinds` | list of object | `["edit", "debug", "refactor", "architecture"]` | Tipos de tarefa com permissão de escrita em um worktree; o resto roda somente leitura. |
| `execution.agent_env_deny` | list of string | `[]` | Padrões de nomes de variáveis de ambiente que os agentes nunca recebem. *(avançada)* |
| `execution.max_output_chars` | integer | `4000000` | Saída mantida em memória por tentativa (o final é mantido). *(avançada)* |
| `execution.stored_output_chars` | integer | `200000` | Saída guardada no registro por tentativa. *(avançada)* |
| `execution.max_parallel_runs` | integer | `2` | Execuções simultâneas a partir da interface web ou MCP. |
| `execution.race_contenders` | integer | `2` | Agentes que competem em --race. |

## Orçamentos

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `budgets` | list of object | `[]` | Limites de gasto por agente e período, e o que fazer ao atingi-los. |

## Registros e privacidade

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `privacy.log_level` | `debug` \| `info` \| `warning` \| `error` | `warning` | Nível do arquivo de log local. |
| `privacy.log_file` | string | `` | Caminho do arquivo de log; vazio o desativa. |
| `privacy.redact_patterns` | list of string | `[]` | Expressões regulares extras ocultadas em logs, registro e interface. |
| `privacy.secret_env_names` | string | `(KEY\|TOKEN\|SECRET\|PASSWORD\|PASSWD\|CREDENTIAL\|AUTH)` | Nomes de variáveis de ambiente cujos valores são sempre ocultados. *(avançada)* |
| `privacy.store_outputs` | boolean | `true` | Guardar as saídas dos agentes no registro. |
| `privacy.retention_days` | integer | `0` | Apagar com purge as execuções mais antigas que estes dias; 0 mantém tudo. |
| `privacy.update_check` | boolean | `false` | Permitir que doctor consulte o PyPI por uma versão nova. Desativado por padrão; nada mais usa a rede. |

## Interface

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `ui.host` | `127.0.0.1` \| `localhost` \| `::1` | `127.0.0.1` | Endereço local em que escuta. |
| `ui.port` | integer | `0` | Porta; 0 escolhe uma livre. |
| `ui.open_browser` | boolean | `true` | Abrir o navegador ao iniciar. |
| `ui.theme` | `auto` \| `light` \| `dark` | `auto` | Tema de cor. |
| `ui.density` | `comfortable` \| `compact` | `comfortable` | Espaçamento da interface. |
| `ui.tour` | boolean | `true` | Mostrar o tour guiado na primeira visita. |
| `ui.shortcuts.panel` | string | `p` | Tecla que abre o painel. *(avançada)* |
| `ui.shortcuts.new` | string | `n` | Tecla que abre nova tarefa. *(avançada)* |
| `ui.shortcuts.history` | string | `h` | Tecla que abre histórico. *(avançada)* |
| `ui.shortcuts.stats` | string | `s` | Tecla que abre estatísticas. *(avançada)* |
| `ui.shortcuts.routes` | string | `r` | Tecla que abre rotas. *(avançada)* |
| `ui.shortcuts.setup` | string | `i` | Tecla que abre configuração inicial. *(avançada)* |
| `ui.shortcuts.settings` | string | `c` | Tecla que abre ajustes. *(avançada)* |
| `ui.shortcuts.theme` | string | `t` | Tecla que abre troca de tema. *(avançada)* |
| `ui.shortcuts.language` | string | `l` | Tecla que abre troca de idioma. *(avançada)* |

## Tela inicial no terminal

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `tui.enabled` | boolean | `true` | Abrir a tela inicial interativa ao executar timonel sem argumentos num terminal. |
| `tui.logo` | `auto` \| `color` \| `mono` \| `ascii` \| `none` | `auto` | Estilo do logo: auto escolhe cor, monocromático ou ASCII conforme o terminal. |
| `tui.compact` | boolean | `false` | Cabeçalho de uma linha em vez do logo. |
| `tui.plain` | boolean | `false` | Texto simples sem caixas nem símbolos, para leitores de tela. |
| `tui.theme` | `auto` \| `dark` \| `light` | `auto` | Cores para terminais escuros ou claros. |
| `tui.show_spend` | boolean | `true` | Mostrar o gasto de hoje e o orçamento restante. |
| `tui.check_on_start` | boolean | `true` | Verificar os agentes em segundo plano ao abrir a tela inicial. |
| `tui.check_timeout_s` | integer | `20` | Segundos de espera por cada verificação de agente antes de mostrar «sem resposta». |
| `tui.status_cache_s` | integer | `300` | Segundos em que uma verificação de agente é reutilizada antes de repetir; 0 desativa o cache. |
| `tui.refresh_s` | number | `1.0` | A cada quantos segundos a barra de status é redesenhada. *(avançada)* |
| `tui.notices` | list of string | `["first_run", "untrusted", "login", "outdated", "not_git"]` | Quais avisos acionáveis mostrar. |
| `tui.placeholder` | string | `` | Dica dentro da caixa de entrada; vazio usa o padrão traduzido. |
| `tui.history` | boolean | `true` | Lembrar o que você digita entre sessões (linhas que parecem segredos nunca são salvas). |
| `tui.history_size` | integer | `1000` | Número máximo de linhas lembradas. *(avançada)* |
| `tui.confirm_run` | boolean | `true` | Pedir confirmação depois de mostrar o plano de uma tarefa. |
| `tui.keys.profile` | string | `s-tab` | Alternar o perfil (cheap, balanced, max). *(avançada)* |
| `tui.keys.mode` | string | `f2` | Alternar entre modo simples e especialista nesta sessão. *(avançada)* |
| `tui.keys.help` | string | `f1` | Mostrar a ajuda. *(avançada)* |
| `tui.keys.exit` | string | `c-d` | Sair da tela inicial. *(avançada)* |

## Notificações

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `notifications.events` | list of object | `["succeeded", "failed"]` | Quais resultados avisam. |
| `notifications.desktop` | boolean | `false` | Notificação na área de trabalho. |
| `notifications.sound` | boolean | `false` | Sino do terminal. |
| `notifications.webhook_url` | string | `` | URL HTTPS que recebe um POST JSON (sem texto da tarefa nem saída, só id, status e tamanho do diff). |

## Hooks

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `hooks.pre_run` | list of object | `[]` | Comandos antes de cada execução; se um falhar, a execução para. *(avançada)* |
| `hooks.post_run` | list of object | `[]` | Comandos após uma execução bem-sucedida. *(avançada)* |
| `hooks.on_failure` | list of object | `[]` | Comandos após uma execução com falha. *(avançada)* |
| `hooks.timeout_s` | integer | `120` | Limite de tempo de cada gancho. *(avançada)* |

## Caminhos

| Chave | Tipo | Padrão | Significado |
|---|---|---|---|
| `paths.data_dir` | string | `` | Diretório de dados; vazio usa o da plataforma. *(avançada)* |
| `paths.ledger` | string | `` | Arquivo do registro; vazio usa data_dir/ledger.sqlite. *(avançada)* |
| `paths.cache_dir` | string | `` | Cache de scripts de instalação baixados; vazio usa data_dir/cache. *(avançada)* |
