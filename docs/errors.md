# Error codes

Every Timonel error starts with a stable code. Search this page for the code you saw.

Cada error de Timonel empieza con un código estable. Busca en esta página el código que viste.

## TMN-1001

**Invalid configuration** · Configuración inválida

Fix the fields listed above, or run 'timonel config validate' to check a file.

*Corrige los campos indicados arriba o ejecuta 'timonel config validate' para revisar un archivo.*

## TMN-1002

**Unknown configuration key** · Clave de configuración desconocida

Run 'timonel config list' to see every key, or 'timonel config schema'.

*Ejecuta 'timonel config list' para ver todas las claves, o 'timonel config schema'.*

## TMN-1003

**Project configuration is not trusted** · La configuración del proyecto no es de confianza

Review .timonel.yaml and run 'timonel trust' if you accept what it runs.

*Revisa .timonel.yaml y ejecuta 'timonel trust' si aceptas lo que ejecuta.*

## TMN-2001

**This folder is not a git repository** · Esta carpeta no es un repositorio git

Run the command inside a git repository, or create one with 'git init'.

*Ejecuta el comando dentro de un repositorio git o crea uno con 'git init'.*

## TMN-2002

**The repository has uncommitted changes** · El repositorio tiene cambios sin confirmar

Commit or stash your changes, then apply again.

*Haz commit o stash de tus cambios y vuelve a aplicar.*

## TMN-2099

**A git command failed** · Falló un comando de git

Read the git message above; 'git status' usually explains what is wrong.

*Lee el mensaje de git de arriba; 'git status' suele explicar qué pasa.*

## TMN-3001

**No usable agent for this task** · No hay ningún agente utilizable para esta tarea

Run 'timonel doctor --fix' to install or log in to an agent, or 'timonel start'.

*Ejecuta 'timonel doctor --fix' para instalar o iniciar sesión en un agente, o 'timonel start'.*

## TMN-3002

**Unknown profile** · Perfil desconocido

Run 'timonel profiles list' to see the available profiles.

*Ejecuta 'timonel profiles list' para ver los perfiles disponibles.*

## TMN-3003

**Unknown agent** · Agente desconocido

Run 'timonel agents list' to see the agents Timonel knows.

*Ejecuta 'timonel agents list' para ver los agentes que conoce Timonel.*

## TMN-3004

**This agent cannot run read-only tasks safely** · Este agente no puede ejecutar tareas de solo lectura con seguridad

Pick another agent with --force, or let Timonel choose.

*Elige otro agente con --force o deja que Timonel elija.*

## TMN-3005

**The classifier gave no valid answer** · El clasificador no dio una respuesta válida

Set classifier.on_failure to heuristic, or check the classifier agents with 'timonel doctor'.

*Pon classifier.on_failure en heuristic o revisa los agentes clasificadores con 'timonel doctor'.*

## TMN-4001

**Unknown run** · Ejecución desconocida

Run 'timonel status' to list runs and their ids.

*Ejecuta 'timonel status' para ver las ejecuciones y sus ids.*

## TMN-4002

**This run cannot be applied** · Esta ejecución no se puede aplicar

Only successful runs that changed files can be applied; check 'timonel status <id>'.

*Solo se aplican ejecuciones correctas que cambiaron archivos; revisa 'timonel status <id>'.*

## TMN-4003

**This run cannot be discarded or resumed now** · Esta ejecución no se puede descartar ni reanudar ahora

Wait for it to finish or cancel it first.

*Espera a que termine o cancélala primero.*

## TMN-5001

**A program was not found** · No se encontró un programa

Install it with 'timonel start' or add its folder to PATH, then open a new terminal.

*Instálalo con 'timonel start' o añade su carpeta al PATH y abre una terminal nueva.*

## TMN-5002

**Unsafe characters for a Windows batch file** · Caracteres inseguros para un archivo por lotes de Windows

Set prompt_via: stdin or file for this agent, or install a native executable.

*Pon prompt_via: stdin o file para este agente, o instala un ejecutable nativo.*

## TMN-5003

**A required API key is missing** · Falta una clave de API necesaria

Run 'timonel keys set <NAME>' to store it in your system keyring, or set the environment variable.

*Ejecuta 'timonel keys set <NOMBRE>' para guardarla en el llavero del sistema, o define la variable de entorno.*

## TMN-7001

**A budget was reached** · Se alcanzó un presupuesto

Raise the limit with 'timonel config set', wait for the next period, or change budgets[].action.

*Sube el límite con 'timonel config set', espera al siguiente periodo o cambia budgets[].action.*

## TMN-8001

**No network connection to the package source** · Sin conexión con el origen de los paquetes

Check your connection. Behind a corporate proxy, set HTTPS_PROXY (and NO_PROXY) and try again.

*Revisa tu conexión. Detrás de un proxy corporativo, define HTTPS_PROXY (y NO_PROXY) e inténtalo de nuevo.*

## TMN-8002

**No supported package manager was found** · No se encontró ningún gestor de paquetes compatible

Install one (winget comes with Windows 11; Homebrew on macOS) or install the tool by hand from its website.

*Instala uno (winget viene con Windows 11; Homebrew en macOS) o instala la herramienta a mano desde su web.*

## TMN-8003

**The installation command failed** · Falló el comando de instalación

Read the output above, fix the cause and run 'timonel start' again; it resumes where it stopped.

*Lee la salida de arriba, corrige la causa y vuelve a ejecutar 'timonel start'; continúa donde se quedó.*

## TMN-8004

**This step needs administrator rights** · Este paso necesita permisos de administrador

Timonel never elevates by itself. Run the command shown in your own terminal, then continue.

*Timonel nunca eleva permisos por sí mismo. Ejecuta el comando mostrado en tu terminal y continúa.*

## TMN-8005

**The downloaded script does not match its checksum** · El script descargado no coincide con su suma de comprobación

Do not run it. Try again later or install by hand from the vendor's website.

*No lo ejecutes. Inténtalo más tarde o instala a mano desde la web del fabricante.*

## TMN-8010

**The system keyring is not available** · El llavero del sistema no está disponible

Use an environment variable for the key instead; Timonel never writes keys to files.

*Usa una variable de entorno para la clave; Timonel nunca escribe claves en archivos.*

## TMN-9001

**Unexpected error** · Error inesperado

Run the command again with --set privacy.log_level=debug and open an issue with the output (check it has no secrets).

*Vuelve a ejecutar con --set privacy.log_level=debug y abre un issue con la salida (revisa que no tenga secretos).*
