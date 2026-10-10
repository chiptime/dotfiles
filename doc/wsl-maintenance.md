# Guía de Mantenimiento de WSL2: Liberación de Espacio y Recuperación de Disco

Esta guía documenta el procedimiento habitual para liberar espacio reservado por el disco virtual de WSL2 (`ext4.vhdx`), solucionar y prevenir el error `EIO (errno 5)` y automatizar el descarte de bloques vacíos.

---

## 1. Diagnóstico: ¿Por qué ocurre el error `EIO (errno 5)`?

```text
WSL (Relay) ERROR: CreateProcessParseCommon:994: getpwnam(bruno) failed 5
WSL (Relay) ERROR: ConfigUpdateLanguage:2519: fopen(/etc/default/locale) failed 5
WSL ERROR: I/O error @util.cpp:1356 (UtilInitGroups)
```

- **Causa**: El error `5` es `EIO` (*Input/Output error* en Linux).
- **Mecanismo**: WSL2 almacena la distribución en un disco virtual de tamaño dinámico (`ext4.vhdx`). Cuando se eliminan archivos dentro de Linux (imágenes de contenedores, cachés, modelos LLM), el espacio se libera en el sistema de archivos `ext4`, pero **el archivo `.vhdx` en Windows no se reduce automáticamente**.
- Si el disco físico de Windows (`C:\`) alcanza el 95-98% de ocupación, Hyper-V no puede realizar escrituras ni asignar bloques al VHDX, provocando fallos de E/S en cascada al leer `/etc/passwd`, `/etc/default/locale` y la sesión de usuario.

### Ubicación del VHDX en este sistema
```text
C:\Users\Bruno\AppData\Local\wsl\{f10e4bdd-6ca6-4ccf-b9c2-63af09a9440e}\ext4.vhdx
```

---

## 2. Protocolo de Mantenimiento Periódico

Ejecutar este protocolo cuando se borren grandes volúmenes de datos en WSL o cuando el disco `C:\` de Windows comience a llenarse.

### Fase A: Limpieza y Trim (Dentro de WSL)

1. **Limpiar contenedores e imágenes huérfanas**:
   ```bash
   # Si usas Podman:
   podman system prune -af

   # Si usas Docker:
   docker system prune -af
   ```

2. **Limpiar paquetes y cachés del sistema**:
   ```bash
   sudo apt autoremove --purge && sudo apt clean
   ```

3. **Descartar bloques vacíos (`fstrim`)**:
   > **Imprescindible**: Este paso marca los bloques libres de `ext4` para que el hipervisor de Windows sepa cuáles puede compactar.
   ```bash
   sudo fstrim -v /
   ```
   *(Debería reportar varios GiB o cientos de GiB descartados).*

---

### Fase B: Compactación física del archivo VHDX (Desde Windows)

1. Abre **PowerShell** o **CMD** en Windows como **Administrador**.
2. Detén todas las instancias de WSL para desbloquear el archivo:
   ```cmd
   wsl --shutdown
   ```
3. Ejecuta `diskpart`:
   ```cmd
   diskpart
   ```
4. Dentro de la consola interactiva `DISKPART>`, ejecuta:
   ```cmd
   select vdisk file="C:\Users\Bruno\AppData\Local\wsl\{f10e4bdd-6ca6-4ccf-b9c2-63af09a9440e}\ext4.vhdx"
   compact vdisk
   exit
   ```
   *(El proceso tardará de 1 a 5 minutos según el volumen a compactar).*

---

## 3. Configuración para Liberación Automática (Sparse VHD)

En versiones modernas de WSL2 (Windows 11 / WSL 2.0+) es posible habilitar el modo **Sparse VHD**, que permite a Windows encoger el archivo `.vhdx` automáticamente en segundo plano cuando se liberan bloques dentro de Linux.

### 1. Activar Sparse en la distribución (Desde PowerShell como Administrador):
```powershell
# 1. Comprobar nombre exacto de la distro:
wsl -l -v

# 2. Convertir el VHDX existente a formato sparse:
wsl --manage Ubuntu --set-sparse true
```
*(Sustituir `Ubuntu` si el nombre listado en `wsl -l -v` es distinto, ej. `Ubuntu-24.04`).*

### 2. Configurar `.wslconfig`
Asegurarse de que en `C:\Users\Bruno\.wslconfig` esté presente la directiva `sparseVhd=true`:

```ini
[wsl2]
networkingMode=mirrored
memory=18GB
processors=28
swap=8GB
pageReporting=true
dnsTunneling=true
firewall=true

[experimental]
autoMemoryReclaim=gradual
sparseVhd=true
```

---

## 4. Procedimiento de Emergencia: Reparar Sistema de Archivos Corrupto

Si tras un apagón, cuelgue o suspensión de Windows, WSL entra en bucle de error `EIO` o no inicia:

1. Apaga WSL desde PowerShell:
   ```powershell
   wsl --shutdown
   ```
2. Ejecuta un chequeo no interactivo del sistema de archivos arrancando como `root`:
   ```powershell
   wsl -u root -e fsck -fy /dev/sdd
   ```
3. Comprueba también la partición NTFS anfitriona en Windows:
   ```powershell
   chkdsk C: /f
   ```
