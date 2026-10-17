# Objetos perdidos

Aplicación web en Flask para registrar y consultar objetos perdidos y encontrados dentro de una universidad. Las personas autenticadas pueden reportar objetos, consultar los reportes publicados y comunicarse entre sí; un administrador revisa y aprueba los reportes antes de su publicación.

Sistema en línea: [https://desly30.pythonanywhere.com](https://desly30.pythonanywhere.com)

## Inicio rápido

En Windows PowerShell:

```powershell
git clone https://github.com/deslyramirez/objetos-perdidos-ucss.git
cd objetos-perdidos-ucss
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask run
```

También puede iniciar la aplicación directamente con:

```powershell
python app.py
```

La aplicación usa SQLite por defecto y crea o actualiza la base de datos local al iniciar. Para ejecutar las pruebas:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:warnings
```

## Funciones actuales

- Registro, inicio y cierre de sesión con protección CSRF.
- Perfil de usuario, avatar, edición de datos y cambio de contraseña.
- Registro de objetos con nombre, descripción, categoría, lugar, fecha del suceso, estado, contacto y fotografía.
- Galería de reportes, búsqueda y filtros por estado, categoría y mes.
- Consulta pública para cualquier persona autenticada y acciones de modificación reservadas a la persona creadora.
- Exportación a Excel con formato (hoja de reportes y hoja de resumen) e importación desde una plantilla de Excel o CSV.
- Solicitudes de eliminación: el usuario solicita y el administrador desactiva el reporte (se conserva en el Excel) o lo elimina definitivamente.
- Mensajes privados entre usuarios por cada reporte (Es mío / Lo encontré / Consulta), con edición y eliminación de mensajes propios.
- Moderación: los reportes nuevos o editados quedan En revisión hasta que el administrador los aprueba o desaprueba con motivo.
- Panel mensual con totales de reportes, estados, evolución diaria, categorías, lugares y reportes recientes.
- Exportación de los reportes correspondientes al mes seleccionado.

## Identidad visual

La interfaz incorpora el logotipo oficial de la Universidad Católica Sedes Sapientiae (UCSS) como recurso local, sin hotlinking ni recoloración. La fuente del recurso es [`https://www.ucss.edu.pe/templates/gk_gamenews/images/logo.png`](https://www.ucss.edu.pe/templates/gk_gamenews/images/logo.png), enlazado desde el sitio oficial [`https://www.ucss.edu.pe/`](https://www.ucss.edu.pe/). Los colores observados en el CSS y HTML oficiales incluyen `#00a2c5`, `#39a1c4`, `#0d97a4`, `#196593` y `#154462`; se usan aquí como referencia visual observada, no como valores certificados por una guía de marca.

Las animaciones son CSS, breves y discretas (entrada, elevación al pasar el puntero y avisos). Se desactivan con `prefers-reduced-motion` y no cambian la semántica, el foco del teclado ni el comportamiento de la aplicación. Esta aplicación no implica patrocinio ni respaldo institucional.

## Modelo de dominio

Cada registro representa un reporte de un objeto perdido o encontrado en el contexto universitario.

| Campo | Descripción |
| --- | --- |
| Objeto | Nombre o identificación breve del objeto. |
| Descripción | Características que facilitan su identificación. |
| Categoría | Documentos, Electrónica, Llaves, Ropa y accesorios, Mochilas y útiles u Otros. |
| Lugar | Sitio donde se perdió o se encontró. |
| Fecha del suceso | Fecha en la que ocurrió la pérdida o el hallazgo. |
| Estado | Perdido, Encontrado o Entregado. |
| Contacto | Correo electrónico o teléfono opcional para coordinar la entrega. |
| Fotografía | Imagen opcional del objeto. |

Existen dos roles. El usuario registra reportes, consulta los publicados y envía mensajes; solo la persona creadora puede editar o marcar como entregado su reporte. El administrador modera los reportes, atiende las solicitudes de eliminación, revisa el panel mensual y realiza la importación y exportación. La cuenta de administrador se crea al iniciar si se define la variable `ADMIN_PASSWORD`.

## Panel mensual y exportación

El panel está disponible en `/dashboard/` y recibe el mes mediante `?mes=YYYY-MM`. Si no se indica un mes, muestra el mes actual según la configuración de zona horaria. Los indicadores y gráficos se calculan usando la fecha del suceso, y el enlace **Exportar mes** genera un archivo Excel con nombre `objetos_YYYY-MM.xlsx`.

El contrato de datos CSV conserva encabezados en inglés para facilitar el intercambio con otras herramientas:

```text
id,name,description,category,location,event_date,status,contact,image_filename,reported_by,created_at
```

Para importar son obligatorios `name`, `location` y `event_date` con formato `YYYY-MM-DD`. `description`, `category`, `status` y `contact` son opcionales. Los valores almacenados de categorías y estados también forman parte del contrato de datos.

## Configuración

La configuración se define mediante variables de entorno:

| Variable | Uso | Valor predeterminado |
| --- | --- | --- |
| `SECRET_KEY` | Clave de firma de sesiones y formularios. | Clave de desarrollo local; debe definirse fuera de desarrollo. |
| `FLASK_DEBUG` | Activa el modo de depuración cuando vale `1`. | Desactivado. |
| `DISPLAY_TIMEZONE` | Zona usada para mostrar fechas y seleccionar el mes actual. | `UTC`. |
| `ADMIN_PASSWORD` | Contraseña inicial de la cuenta de administrador. | Sin definir; no se crea la cuenta. |

La aplicación limita las cargas a 5 MB y guarda las fotografías en `static/uploads` y los avatares en `static/avatars`. No se requiere una configuración adicional para SQLite durante el desarrollo local.

## Estructura del proyecto

```text
objetos-perdidos-ucss/
├── app.py                 # Fábrica de la aplicación y configuración de errores
├── auth.py                # Registro, autenticación y cierre de sesión
├── config.py              # Configuración y rutas locales
├── dashboard.py           # Panel y exportación mensual
├── domain.py              # Categorías, estados y utilidades del dominio
├── forms.py               # Formularios y validaciones
├── messages.py            # Mensajes privados entre usuarios
├── admin.py               # Moderación y solicitudes de eliminación
├── excel.py               # Exportación e importación en Excel
├── main.py                # Reportes, búsqueda e importación/exportación
├── models.py              # Modelos persistentes
├── profile.py             # Perfil, avatar y contraseña
├── schema.py              # Actualización aditiva de bases heredadas
├── templates/             # Vistas Jinja en español
├── static/                # Hojas de estilo, imágenes y cargas
└── tests/                 # Pruebas automatizadas
```

## Seguridad y datos

- Las operaciones POST usan protección CSRF.
- Las contraseñas se almacenan mediante hash; no se guardan en texto plano.
- Las cargas de imágenes se validan y se renombran antes de guardarse.
- Las redirecciones externas se rechazan.
- La clave de desarrollo incorporada en `config.py` no debe utilizarse en un despliegue real; defina `SECRET_KEY`.
- La base de datos existente se actualiza de forma aditiva. Las columnas heredadas que ya no se muestran en la interfaz se conservan para evitar una migración destructiva.
- Las fechas almacenadas se tratan como UTC sin zona horaria. Si la zona solicitada no está disponible porque falta `tzdata`, la aplicación utiliza UTC como alternativa.

## Limitaciones conocidas

- La interfaz se sirve con Tailwind Play CDN, por lo que no incluye un proceso de compilación para producción.
- La aplicación usa SQLite y está desplegada en PythonAnywhere (plan gratuito).
- Las imágenes de `screenshots/` muestran la interfaz anterior y no se han regenerado después de la conversión al dominio de objetos perdidos.

## Capturas de pantalla

Las capturas históricas están disponibles en [`screenshots/`](screenshots/). Incluyen las pantallas de inicio de sesión, registro, perfil, panel y formularios de la interfaz anterior.

## Autoría

Sistema de objetos perdidos desarrollado por Desly Ramírez para el curso Gestión de la Configuración del Software (UCSS), a partir de la base [inventory-management-flask](https://github.com/Rumaisas-islam/inventory-management-flask) de Rumaisa Islam. La adaptación al dominio de objetos perdidos, la moderación, los mensajes, la importación y exportación en Excel y el despliegue son trabajo propio.

## Licencia

El código base original se distribuye bajo la licencia MIT de su autora; consulte [`LICENSE`](LICENSE).

Modificaciones y nuevas funcionalidades © 2026 Desly Ramírez. Todos los derechos reservados sobre estas partes: no se autoriza su copia, modificación ni redistribución sin permiso de la autora.
