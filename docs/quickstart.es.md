# Inicio rápido

Esta guía no da nada por sabido. Si una palabra es nueva, `timonel learn` la explica (por ejemplo
`timonel learn worktree`). Usa `--lang es` o `TIMONEL_LANG=es` para ver Timonel en español.

## 1. Instala Timonel

Necesitas Python 3.11 o superior. Después, en una terminal:

=== "Windows (PowerShell)"

    ```powershell
    Invoke-WebRequest https://raw.githubusercontent.com/EduMayMar/Timonel/main/install/install.ps1 -OutFile install.ps1
    Get-Content install.ps1   # léelo antes
    powershell -ExecutionPolicy Bypass -File install.ps1
    ```

=== "macOS / Linux"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/EduMayMar/Timonel/main/install/install.sh -o install.sh
    less install.sh           # léelo antes
    sh install.sh
    ```

=== "Con pipx o uv"

    ```bash
    pipx install git+https://github.com/EduMayMar/Timonel
    # o
    uv tool install git+https://github.com/EduMayMar/Timonel
    ```

El script muestra el único comando que va a ejecutar (`uv tool install` o `pipx install`) y pregunta
antes. Nunca pide permisos de administrador. Hasta la primera versión en PyPI, define
`TIMONEL_SOURCE=git+https://github.com/EduMayMar/Timonel` antes de ejecutarlo.

## 2. Prueba la demo

```bash
timonel demo
```

Crea un proyecto pequeño con un fallo y dos agentes simulados. Verás cómo el agente barato no pasa
las pruebas, Timonel escala al más fuerte y las pruebas pasan. No se instala nada ni se llama a
ninguna IA.

## 3. Configura tus agentes

```bash
timonel start
```

El asistente revisa tu sistema (Git, Node.js, gestores de paquetes) y los agentes que elijas. Para
cada pieza que falte explica qué es, enseña el comando exacto y su tamaño aproximado, y pregunta:
**instalar**, **saltar**, **explicar** o **salir**. Con `--dry-run` solo ves el plan.

Cuando un agente necesita iniciar sesión, Timonel abre el inicio de sesión oficial del proveedor en tu
terminal. Tu contraseña va al proveedor, nunca a Timonel. Las claves de API, si usas alguna, se
escriben sin mostrarse y se guardan en el llavero del sistema.

Estudiantes: el [GitHub Student Developer Pack](https://education.github.com/pack) incluye GitHub
Copilot; `timonel start` te lo indica cuando eliges Copilot.

## 4. Tu primera tarea

```bash
cd ruta/a/tu/proyecto
timonel plan "Arregla el fallo cuando config.yaml está vacío"
timonel run "Arregla el fallo cuando config.yaml está vacío"
timonel diff <id>      # mira el cambio
timonel apply <id>     # quédatelo
timonel discard <id>   # o descártalo
```

## 5. ¿Prefieres el navegador?

```bash
timonel ui
```

abre una página local con recorrido guiado, lista de configuración, progreso en vivo y ajustes.

## Si algo falla

Cada error tiene un código como `TMN-2001` con la causa, la solución y un enlace a
[la lista de errores](errors.md). `timonel doctor --fix` lo revisa todo y propone arreglos. Añade
`--explain` a cualquier comando para ver qué hará antes de hacerlo.
