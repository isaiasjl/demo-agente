# Publicar la demo en Azure y Teams

Estos pasos no los he podido ejecutar: en esta máquina no hay `az`, Docker ni acceso a una suscripción de Azure. Los comandos son de referencia, revísalos contra la documentación antes de correrlos. Lo que sí probé en local está en el README.

## Qué se publica

- **Backend** (`demo_agente.backend`): el agente con FastAPI. Solo lo llama el front, así que no necesita ser público.
- **Front** (`demo_agente.front`): recibe los mensajes de Teams en `/api/messages`. Tiene que ser público y con HTTPS.
- **Azure Bot**: el recurso que conecta Teams con el front.
- **Paquete de Teams**: un `.zip` con el manifest, para instalar el bot.
- **Modelo**: un proyecto de Foundry con un modelo desplegado. Lo usa el backend.

## Qué necesitas

- Una suscripción de Azure donde puedas crear un Azure Bot. Para desplegar también hacen falta Container Registry y Container Apps. El rol Contributor sobre un grupo de recursos alcanza.
- Un proyecto de Foundry con un modelo desplegado. Te hace falta el endpoint del proyecto y el nombre del despliegue.
- Poder registrar una app en Entra ID. Si no, que alguien la cree y te dé el client ID, el tenant ID y un secreto.
- Que tu Teams permita instalar apps personalizadas. Lo activa el administrador de Teams.
- Que Container Apps y Foundry existan en la región que vayas a usar. En mIA se planea México Central.

## 1. Registrar la app en Entra ID

Es la identidad del bot. En el portal de Azure: Microsoft Entra ID, Registros de aplicaciones, Nuevo registro. Elige un solo inquilino (single tenant) y anota dos datos:

- el Application (client) ID, que también es el id del bot
- el Directory (tenant) ID

Después, en Certificados y secretos, crea un secreto de cliente y copia el valor en ese momento. Luego ya no se puede volver a ver.

## 2. Crear el Azure Bot

En el portal, Crear un recurso y busca Azure Bot. Tipo de aplicación Single Tenant, usando la app del paso anterior. Luego, en Canales, agrega Microsoft Teams.

El Messaging endpoint se llena después, cuando el front ya tenga una dirección pública. Siempre termina en `/api/messages`.

## 3A. Probar desde tu máquina con un túnel

Es lo más rápido para ver el bot en Teams sin desplegar nada. Un túnel le da a tu `localhost:3978` una dirección HTTPS pública, que es lo que necesita Azure Bot para llegar al front.

1. Instala la CLI de dev tunnels con `winget install Microsoft.devtunnel` e inicia sesión con `devtunnel user login`.
2. Pon las credenciales del paso 1 en tu `.env`:

   ```
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=<application-client-id>
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=<valor-del-secreto>
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=<directory-tenant-id>
   ```

   Con eso el front arranca en modo producción y exige el token de Entra ID, que es lo que manda Azure Bot.
3. Arranca el backend y el front, cada uno en su terminal, como en el README.
4. Arranca el túnel con `devtunnel host -p 3978 --allow-anonymous` y copia la URL que sale en `Connect via browser`.
5. En el Azure Bot, Configuración, pon como Messaging endpoint `<url-del-túnel>/api/messages`.
6. En el mismo recurso, prueba con "Probar en Web Chat". Si contesta, sigue con el paso 4.

Funciona mientras tu máquina esté prendida y el túnel y las dos terminales sigan abiertos. Aquí el modelo es Claude con tu plan Pro, que ve datos de tu cuenta, como tu correo, así que no lo uses con otras personas.

## 3B. Desplegar en Azure con Container Apps

Los comandos son de PowerShell. `az acr build` construye la imagen en Azure, no hace falta Docker.

Primero el grupo de recursos, el registro y el entorno de Container Apps:

```powershell
$RG = "rg-demo-agente"; $LOC = "<region>"; $ACR = "<nombre-unico-de-acr>"; $ENVCA = "cae-demo-agente"
az login
az account set --subscription "<id-de-la-suscripcion>"
az group create -n $RG -l $LOC

az acr create -g $RG -n $ACR --sku Basic --admin-enabled true
az acr build -r $ACR -t demo-backend:v1 --build-arg APP=backend .
az acr build -r $ACR -t demo-front:v1   --build-arg APP=front .
$ACRPASS = az acr credential show -n $ACR --query "passwords[0].value" -o tsv

az containerapp env create -g $RG -n $ENVCA -l $LOC
```

La cuenta de administrador del registro es solo para la demo. En un proyecto real se usa una identidad administrada con el rol AcrPull.

Después el backend. Va con una sola réplica porque las sesiones están en memoria, y con ingress interno porque solo lo llama el front:

```powershell
$CLAVE = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")   # clave compartida entre front y backend

az containerapp create -g $RG -n demo-backend --environment $ENVCA `
  --image "$ACR.azurecr.io/demo-backend:v1" `
  --registry-server "$ACR.azurecr.io" --registry-username $ACR --registry-password $ACRPASS `
  --ingress internal --target-port 8088 --min-replicas 1 --max-replicas 1 `
  --system-assigned `
  --secrets api-key=$CLAVE `
  --env-vars PORT=8088 REQUIRE_API_KEY=true BACKEND_API_KEY=secretref:api-key ENABLE_DOCS=false `
             MODEL_PROVIDER=foundry FOUNDRY_PROJECT_ENDPOINT="<endpoint-del-proyecto>" FOUNDRY_MODEL="<nombre-del-despliegue>"
```

Para que el backend pueda llamar a Foundry, dale permiso a su identidad. El rol puede llamarse "Azure AI User" o "Foundry User", según el portal:

```powershell
$PRINCIPAL = az containerapp show -g $RG -n demo-backend --query identity.principalId -o tsv
az role assignment create --assignee-object-id $PRINCIPAL --assignee-principal-type ServicePrincipal `
  --role "Azure AI User" --scope "<id-del-recurso-de-Foundry>"
```

Por último el front, con las credenciales del paso 1 y ingress externo:

```powershell
$BACKEND = az containerapp show -g $RG -n demo-backend --query properties.configuration.ingress.fqdn -o tsv

az containerapp create -g $RG -n demo-front --environment $ENVCA `
  --image "$ACR.azurecr.io/demo-front:v1" `
  --registry-server "$ACR.azurecr.io" --registry-username $ACR --registry-password $ACRPASS `
  --ingress external --target-port 3978 --min-replicas 1 --max-replicas 1 `
  --secrets api-key=$CLAVE bot-secret="<valor-del-secreto-de-cliente>" `
  --env-vars PORT=3978 BACKEND_URL="https://$BACKEND" BACKEND_API_KEY=secretref:api-key `
             CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID="<application-client-id>" `
             CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=secretref:bot-secret `
             CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID="<directory-tenant-id>"

az containerapp show -g $RG -n demo-front --query properties.configuration.ingress.fqdn -o tsv   # dominio del front
```

Comprueba que responda: `https://<dominio-del-front>/health` debe devolver `{"status":"ok"}`. Los logs se ven con `az containerapp logs show -g $RG -n demo-front --follow`.

Ahora sí pon el Messaging endpoint del Azure Bot (`https://<dominio-del-front>/api/messages`) y prueba con Web Chat.

## 4. Instalar en Teams

```powershell
uv run python teams/empaquetar.py --bot-id <application-client-id> --dominio <dominio-del-front-o-del-túnel>
```

Genera `teams/demo-teams.zip`. En Teams ve a Aplicaciones, Administrar tus aplicaciones, Cargar una aplicación y elige "Cargar una aplicación personalizada". Abre el bot y escribe "hola": debe salir la bienvenida. Con "¿a cuánto está el dólar?" debe usar la herramienta.

## Si algo falla

- **Web Chat no contesta o da 401.** Revisa CLIENTID, TENANTID y el secreto en el front, y que el Azure Bot sea Single Tenant con esa misma app.
- **El front contesta "no está disponible".** Mira los logs del front. Casi siempre `BACKEND_URL` está mal o `BACKEND_API_KEY` no es la misma en los dos.
- **El backend da 502.** Mira sus logs. Suele ser Foundry: el endpoint, el nombre del despliegue o el permiso de la identidad.
- **El front se cierra al arrancar** diciendo que el modo local sin token solo puede escuchar en 127.0.0.1. Faltan las credenciales del Azure Bot: define CLIENTID, CLIENTSECRET y TENANTID.
- **Teams rechaza el `.zip`.** El mensaje dice qué campo falla. El manifest no lo he probado en Teams.
- **El bot contesta pero Teams muestra un error.** La respuesta tardó más de unos 15 segundos. Mira los límites de abajo.

## Límites de la demo

- Las sesiones están en memoria: una sola réplica, y si se reinicia se pierden las conversaciones. Con varias réplicas hay que guardarlas fuera, por ejemplo en Cosmos DB o Redis.
- No hay mensajes proactivos. Si el agente tarda más de lo que espera Teams (unos 15 segundos), la respuesta puede llegar tarde o Teams puede reintentar. El front ignora la Activity repetida, pero lo ideal es avisar "estoy trabajando en ello" y mandar el resultado después como mensaje proactivo.
- No hay streaming. El SDK lo soporta en chats personales y el ejemplo oficial lo usa.
- Front y backend comparten una clave. Para la demo basta.
- No hay SSO ni permisos por usuario: el backend no sabe quién preguntó.

## Qué cambia en mIA

- El front llama al backend a través de APIM, con OAuth2 y cuotas, en lugar de una clave compartida.
- Todo va en red privada (Private Endpoints y DNS privado), no solo el backend.
- Se despliega con Terraform y pipelines DEV, UAT y PROD con aprobaciones, no con `az` a mano.
- El estado de las conversaciones va en un almacén externo, por ejemplo Cosmos DB.
- Las herramientas llaman APIs reales o Fabric, por MCP u OpenAPI, en lugar de datos de ejemplo. Para Fabric hay que resolver los permisos por usuario, con un token OBO.
- Los logs van a Application Insights y Log Analytics.
