# 🌡️ Panel de Temperaturas

> Monitor de hardware en tiempo real para un **Proxmox**, con picos máximos, historial gráfico, **alarmas por Telegram** y **panel de ajustes**. **Solo red local** 🌐🔒

![Panel de Temperaturas](screenshot.png)

Dashboard oscuro y moderno que muestra la **temperatura en vivo** de CPU, GPU y discos, se actualiza configurablemente (**0.1 s – 5 s**), guarda el **pico máximo histórico** incluso si cierras la página, dibuja una **sparkline** por sensor y te **avisa por Telegram** cuando la CPU se dispara.

---

## ✨ Características

- 🌡️ **4 tarjetas de temperatura**: CPU, GPU, Disco SDA, Disco SDB.
- ⏱️ **Actualización configurable**: 0.1 s / 0.25 s / 0.5 s / 1 s / 2 s / 5 s.
- 🎨 **Colores del gauge configurables** (umbral verde→amarillo→rojo y referencia máxima).
- 📈 **Sparkline** en cada tarjeta (curva SVG suave de la evolución reciente).
- 🔺 **Pico máximo registrado** con fecha/hora — persiste aunque cierres la web.
- ↺ **Botón "Reiniciar máximos"**.
- 🚨 **Alarma por Telegram**: si la CPU supera el umbral, aviso + temperatura **cada segundo durante N segundos** (seguimiento en vivo).
- 🛠️ **Panel de ajustes** en la propia web: edita umbrales, colores, velocidad de refresco, token del bot y chat ID, con botón de **prueba de alarma**.
- 💾 **Configuración persistente** en `config.json` (editable e inmediata).
- ⚙️ **Sistema**: uso de CPU, memoria RAM, tiempo encendido, red local, estado seguro.
- 💾 **Backend ligero** en Python puro (stdlib, cero dependencias).

---

## 🧱 Arquitectura

El hardware (sensores) solo es visible desde el **host Proxmox**, así que el sistema se divide en **dos procesos** + **una alarma**:

```
┌───────────────────────────────┐        ┌────────────────────────────────┐
│  HOST PROXMOX  (el host)      │        │  LXC "Webs" (el panel)      │
│                              │  HTTP  │                                │
│  host_server.py   :8686      │◄──────►│  lxc_server.py   :8787         │
│  • lee sensores /sys          │        │  • sirve el panel (index.html) │
│  • sampler 5s en 2º plano     │        │  • proxy a :8686 (GET+POST)    │
│  • picos → peaks.json         │        │  • acceso: http://:8787        │
│  • histórico de temperaturas  │        │                                │
│  • config.json (ajustes)      │        │                                │
│  • /api/config + /api/reset   │        │                                │
│                              │        │                                │
│  temp-alarm.service (alarm.py)│        │                                │
│  • vigila CPU, envía Telegram │        │                                │
└───────────────────────────────┘        └────────────────────────────────┘
```

> **¿Por qué dos?** Los LXC/contenedores **no ven el hardware real** (`/sys/class/hwmon`). El recopilador debe vivir en el host; el panel web puede ir aparte (aquí, en el LXC de webs con pm2).

---

## 📦 Archivos

| Archivo | Rol |
|---|---|
| `index.html` | 🎨 El panel web + ⚙️ pantalla de ajustes |
| `host_server.py` | 🛰️ Recopilador del host + picos + historial + `config.json` |
| `lxc_server.py` | 🌐 Panel-proxy (reenvía GET y POST) |
| `alarm.py` | 🚨 Alarma de temperatura → Telegram (lee la config) |
| `minipc-monitor.service` | ⚙️ Servicio systemd del recopilador |
| `temp-alarm.service` | ⚙️ Servicio systemd de la alarma Telegram |

---

## 🚀 Instalación y configuración

### 1) Recopilador + alarma — HOST Proxmox

```bash
mkdir -p /opt/monitor
cp host_server.py alarm.py /opt/monitor/
cp minipc-monitor.service temp-alarm.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now minipc-monitor temp-alarm
systemctl status minipc-monitor temp-alarm   # → ambos active
```

- Recopilador escucha en `0.0.0.0:8686`.
- Guarda picos en `/opt/monitor/peaks.json` y configuración en `/opt/monitor/config.json`.

> ✏️ Ajusta en `host_server.py`: `DISKS` (tus discos), `SAMPLE_S` (muestreo de picos).

### 2) Panel — LXC / donde sea

```bash
mkdir -p /opt/minipc-panel
cp lxc_server.py index.html /opt/minipc-panel/
cd /opt/minipc-panel
pm2 start lxc_server.py --name minipc-panel --interpreter python3
pm2 save
```

> ✏️ En `lxc_server.py`: `HOST` (IP del recopilador) y `HTML`.

### 3) Abre el panel

```
http://<ip-del-lxc>:8787
```

---

## 🛠️ Panel de ajustes

Entra en la pestaña **🛠 Ajustes** (barra lateral). Ahí puedes configurar, y se guarda al instante en `config.json`:

- **🌡️ Temperaturas**: umbral verde (≤), umbral amarillo (≤), referencia máxima del gauge.
- **🔄 Actualización**: intervalo del panel (100 ms – 5 s).
- **🚨 Alarma Telegram**: umbral de alarma (°C), segundos de seguimiento, histéresis.
- **🤖 Bot de Telegram**: token del bot + chat ID, con instrucciones y botón **🧪 Probar alarma**.

Boton **💾 Guardar** aplica todo; se persiste en `config.json` del host.

---

## 🔌 API

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/stats` | GET | `current`, `peaks`, `history` |
| `/api/peaks` | GET | Solo picos máximos |
| `/api/reset` | POST | Reinicia picos |
| `/api/config` | GET / POST | Lee / guarda la configuración |
| `/api/test_alarm` | POST | Envía un mensaje de prueba a Telegram |

---

## 🚨 Alarma por Telegram

Cuando la **CPU supera el umbral** (por defecto 95 °C):

```
🚨 ALARMA temperatura
CPU a 96.3 °C (umbral 95 °)
Seguimiento 20 s…
🔴 CPU: 96.1 °C
🔴 CPU: 95.8 °C
🟠 CPU: 94.2 °C
… (cada segundo durante los segundos configurados)
✅ Fin del seguimiento.
```

Tiene **histéresis** (no re-alarma hasta bajar X ° por debajo del umbral). El token y chat se configuran en el panel o en `config.json`.

---

## 🛠️ Requisitos

- 🐍 **Python 3** (solo **stdlib**): `http.server`, `urllib`, `json`, `subprocess`.
- 🐧 **Linux** con `smartctl` para temperatura de discos (opcional).
- 🌐 Navegador moderno para el panel.

---

## 🔒 Notas

- El panel es **solo red local** (no lo expongas a Internet).
- `smartctl` se ejecuta cacheado (30 s) para no penalizar el rendimiento.
- Los **picos y la configuración** son **persistentes** en disco (`peaks.json`, `config.json`), sobreviven reinicios, y el botón *Reiniciar máximos* los borra.

---

Hecho con 💙 para tener el Mini PC siempre bajo control. ¡Que no explote! 🔥