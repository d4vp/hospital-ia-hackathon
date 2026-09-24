"""UI translations (Spanish / English). Usage: t("key") with the language in session_state."""
import streamlit as st

LANGUAGES = {"es": "Español", "en": "English"}

STRINGS: dict[str, dict[str, str]] = {
    # App / navigation
    "app_name": {"es": "Hospital Susana López de Valencia", "en": "Hospital Susana López de Valencia"},
    "app_tagline": {"es": "Inteligencia operativa", "en": "Operations intelligence"},
    "nav_home": {"es": "Inicio", "en": "Home"},
    "nav_agent": {"es": "Agente IA", "en": "AI agent"},
    "nav_dashboard": {"es": "Tablero", "en": "Dashboard"},
    "nav_reports": {"es": "Reportes", "en": "Reports"},
    "nav_alerts": {"es": "Alertas", "en": "Alerts"},
    "nav_upload": {"es": "Carga de datos", "en": "Data upload"},
    "nav_users": {"es": "Usuarios", "en": "Users"},
    "nav_login": {"es": "Ingresar", "en": "Sign in"},
    "section_analysis": {"es": "Análisis", "en": "Analysis"},
    "section_admin": {"es": "Administración", "en": "Administration"},
    # Accessibility panel
    "a11y_title": {"es": "Accesibilidad", "en": "Accessibility"},
    "language": {"es": "Idioma", "en": "Language"},
    "theme": {"es": "Contraste", "en": "Contrast"},
    "theme_light": {"es": "Claro", "en": "Light"},
    "theme_dark": {"es": "Oscuro", "en": "Dark"},
    "theme_high_contrast": {"es": "Alto contraste", "en": "High contrast"},
    "font_size": {"es": "Tamaño del texto", "en": "Text size"},
    "signed_in_as": {"es": "Sesión de", "en": "Signed in as"},
    "role_admin": {"es": "Administrador", "en": "Administrator"},
    "role_user": {"es": "Usuario", "en": "User"},
    "logout": {"es": "Cerrar sesión", "en": "Sign out"},
    # Login
    "login_title": {"es": "Ingresa con tu cuenta del hospital", "en": "Sign in with your hospital account"},
    "email": {"es": "Correo", "en": "Email"},
    "password": {"es": "Contraseña", "en": "Password"},
    "login_button": {"es": "Ingresar", "en": "Sign in"},
    "login_failed": {"es": "Correo o contraseña incorrectos.", "en": "Wrong email or password."},
    "login_help": {"es": "¿No tienes cuenta? Pídela al administrador del sistema.",
                   "en": "No account yet? Ask the system administrator."},
    # Errors
    "api_unreachable": {"es": "No hay conexión con el servidor. Verifica que el backend esté en ejecución.",
                        "en": "Cannot reach the server. Check that the backend is running."},
    "session_expired": {"es": "Tu sesión expiró. Ingresa de nuevo.", "en": "Your session expired. Please sign in again."},
    "no_data_loaded": {"es": "Todavía no hay datos cargados. Un administrador debe subir el archivo en «Carga de datos».",
                       "en": "No data loaded yet. An administrator must upload the workbook in “Data upload”."},
    "forbidden": {"es": "Esta sección es solo para administradores.", "en": "This section is for administrators only."},
    # Home
    "home_intro": {"es": "Consulta la operación del hospital en lenguaje natural, revisa indicadores y recibe alertas.",
                   "en": "Ask about hospital operations in plain language, review indicators and receive alerts."},
    "data_until": {"es": "Datos hasta", "en": "Data up to"},
    "try_asking": {"es": "Prueba preguntando", "en": "Try asking"},
    "active_alerts": {"es": "Alertas activas", "en": "Active alerts"},
    "see_all_alerts": {"es": "Ver todas las alertas", "en": "See all alerts"},
    "open_dashboard": {"es": "Abrir tablero", "en": "Open dashboard"},
    # Agent
    "agent_intro": {"es": "Pregunta sobre camas, tiempos de espera, medicamentos, cirugías o demanda. "
                          "Puedes repreguntar, por ejemplo «¿y en pediatría?».",
                    "en": "Ask about beds, waiting times, medications, surgeries or demand. "
                          "Follow-ups work too, e.g. “and in paediatrics?”."},
    "chat_placeholder": {"es": "Escribe tu pregunta…", "en": "Type your question…"},
    "new_conversation": {"es": "Nueva conversación", "en": "New conversation"},
    "thinking": {"es": "Consultando los datos…", "en": "Querying the data…"},
    "engine_llm": {"es": "Respuesta con IA", "en": "AI answer"},
    "engine_fallback": {"es": "Modo contingencia (sin IA externa)", "en": "Contingency mode (no external AI)"},
    "technical_detail": {"es": "Detalle técnico (solo administradores)", "en": "Technical detail (admins only)"},
    "result_table": {"es": "Datos de la respuesta", "en": "Answer data"},
    "q1": {"es": "¿Cuántas camas de UCI están ocupadas hoy?", "en": "How many ICU beds are occupied today?"},
    "q2": {"es": "¿Cuáles son los medicamentos con menos de 5 días de inventario?",
           "en": "Which medications have less than 5 days of inventory?"},
    "q3": {"es": "¿Cuál es el tiempo de espera promedio en urgencias en la última semana?",
           "en": "What is the average ER waiting time in the last week?"},
    "q4": {"es": "¿Qué servicio tiene más pacientes ingresados este mes?",
           "en": "Which service has the most admitted patients this month?"},
    # Dashboard
    "filters": {"es": "Filtros", "en": "Filters"},
    "date_range": {"es": "Rango de fechas (ingreso)", "en": "Date range (admission)"},
    "bed_group": {"es": "Servicio", "en": "Service"},
    "specialty": {"es": "Especialidad", "en": "Specialty"},
    "all": {"es": "Todos", "en": "All"},
    "card_admissions": {"es": "Ingresos del periodo", "en": "Admissions in period"},
    "card_occupancy": {"es": "Ocupación actual", "en": "Current occupancy"},
    "card_er_wait": {"es": "Espera en urgencias", "en": "ER waiting time"},
    "card_low_stock": {"es": "Medicamentos con stock bajo", "en": "Low-stock medications"},
    "card_surgery": {"es": "Cirugías realizadas", "en": "Surgeries performed"},
    "beds_occupied": {"es": "{occ} de {beds} camas", "en": "{occ} of {beds} beds"},
    "median_minutes": {"es": "mediana {m} min", "en": "median {m} min"},
    "under_days": {"es": "menos de {d} días · simulado", "en": "under {d} days · simulated"},
    "of_scheduled": {"es": "de lo programado", "en": "of scheduled"},
    "tab_occupancy": {"es": "Ocupación", "en": "Occupancy"},
    "tab_waits": {"es": "Tiempos de espera", "en": "Waiting times"},
    "tab_surgery": {"es": "Cirugías", "en": "Surgeries"},
    "tab_demand": {"es": "Demanda", "en": "Demand"},
    "tab_meds": {"es": "Medicamentos", "en": "Medications"},
    "tab_patients": {"es": "Pacientes", "en": "Patients"},
    "chart_occupancy_now": {"es": "Ocupación estimada por servicio (hoy)", "en": "Estimated occupancy by service (today)"},
    "chart_occupancy_trend": {"es": "Evolución diaria de la ocupación", "en": "Daily occupancy trend"},
    "chart_wait_triage": {"es": "Espera promedio por nivel de triage", "en": "Average wait by triage level"},
    "chart_wait_shift": {"es": "Espera promedio por turno", "en": "Average wait by shift"},
    "chart_surgery": {"es": "Cirugías programadas vs. realizadas por mes", "en": "Scheduled vs. performed surgeries by month"},
    "chart_specialty": {"es": "Demanda por especialidad", "en": "Demand by specialty"},
    "chart_area": {"es": "Demanda por área de servicio", "en": "Demand by service area"},
    "chart_top_meds": {"es": "Mayor consumo", "en": "Highest consumption"},
    "chart_low_meds": {"es": "Menor rotación", "en": "Lowest turnover"},
    "chart_census_by_group": {"es": "Pacientes en cama por servicio (censo diario)", "en": "Patients in bed by service (daily census)"},
    "surgeries_scheduled": {"es": "Programadas", "en": "Scheduled"},
    "surgeries_performed": {"es": "Realizadas", "en": "Performed"},
    "completion": {"es": "Cumplimiento", "en": "Completion"},
    "surgery_note": {"es": "Una cirugía se considera realizada si el código CUPS programado aparece en los servicios "
                           "prestados del mismo ingreso. Se excluyen programaciones sin ingreso asociado.",
                     "en": "A surgery counts as performed when the scheduled CUPS code appears in the services "
                           "provided for the same admission. Schedules without a linked admission are excluded."},
    "chart_admissions_group": {"es": "Ingresos por servicio", "en": "Admissions by service"},
    "low_stock_title": {"es": "Stock bajo (inventario simulado)", "en": "Low stock (simulated inventory)"},
    "synthetic_note": {"es": "El HIS no incluye inventario: el stock es simulado; el consumo diario es real.",
                       "en": "The HIS has no inventory table: stock is simulated; daily consumption is real."},
    "census_note": {"es": "La ocupación se estima con la última atención registrada de cada ingreso y la capacidad "
                          "con las camas distintas observadas. Valores sobre 100% indican sobreocupación o camas no registradas.",
                    "en": "Occupancy is estimated from each admission's last recorded activity, and capacity from the "
                          "distinct beds observed. Values above 100% mean overcrowding or unregistered beds."},
    "patients_note": {"es": "Listado sin datos sensibles: no incluye nombre, documento, fecha de nacimiento, "
                            "motivo de consulta ni diagnóstico específico.",
                      "en": "De-identified list: no name, document, birth date, chief complaint or specific diagnosis."},
    "page": {"es": "Página", "en": "Page"},
    "rows_total": {"es": "{n} registros", "en": "{n} records"},
    "no_alerts": {"es": "Sin alertas activas.", "en": "No active alerts."},
    "minutes": {"es": "minutos", "en": "minutes"},
    "patients_word": {"es": "pacientes", "en": "patients"},
    # Alerts
    "alerts_intro": {"es": "Reglas automáticas sobre ocupación, inventario, tiempos de espera y cirugías. "
                           "Las alertas nuevas se envían a Telegram a través de n8n.",
                     "en": "Automatic rules on occupancy, inventory, waiting times and surgeries. "
                           "New alerts are sent to Telegram through n8n."},
    "evaluate_now": {"es": "Evaluar y notificar ahora", "en": "Evaluate and notify now"},
    "evaluated": {"es": "Evaluación lista: {active} activas, {new} nuevas notificadas, {resolved} resueltas.",
                  "en": "Evaluation done: {active} active, {new} new notified, {resolved} resolved."},
    "severity_critical": {"es": "Crítica", "en": "Critical"},
    "severity_high": {"es": "Alta", "en": "High"},
    "severity_medium": {"es": "Media", "en": "Medium"},
    "notified": {"es": "Notificada", "en": "Notified"},
    "not_notified": {"es": "Sin notificar", "en": "Not notified"},
    "since": {"es": "desde", "en": "since"},
    # Reports
    "reports_intro": {"es": "Estadística inferencial sobre los datos reales para apoyar decisiones. "
                            "Cada resultado incluye su interpretación.",
                      "en": "Inferential statistics on the real data to support decisions. "
                            "Each result includes its interpretation."},
    "rep_wait": {"es": "Tiempo de espera en urgencias (intervalos de confianza)", "en": "ER waiting time (confidence intervals)"},
    "rep_occupancy": {"es": "Ocupación por servicio (IC 95%, últimos 30 días)", "en": "Occupancy by service (95% CI, last 30 days)"},
    "rep_compare": {"es": "¿Cambió la espera respecto al mes anterior?", "en": "Did waiting time change vs. last month?"},
    "rep_trends": {"es": "Tendencias de ingresos y alertas predictivas", "en": "Admission trends and predictive alerts"},
    "rep_root": {"es": "Causas de la demora en urgencias", "en": "Root causes of ER delay"},
    "rep_method": {"es": "Método", "en": "Method"},
    "weekly_admissions": {"es": "Ingresos semanales", "en": "Weekly admissions"},
    "week": {"es": "Semana", "en": "Week"},
    "mean_ci": {"es": "Media e IC 95%", "en": "Mean and 95% CI"},
    # Upload
    "upload_intro": {"es": "Sube el archivo DateBaseHIS.xlsx (7 hojas). Se reemplazan los datos anteriores y "
                           "se eliminan los registros que ya no estén en el archivo.",
                     "en": "Upload DateBaseHIS.xlsx (7 sheets). Previous data is replaced and records that are "
                           "no longer in the file are removed."},
    "choose_file": {"es": "Archivo Excel (.xlsx)", "en": "Excel file (.xlsx)"},
    "upload_button": {"es": "Subir y procesar", "en": "Upload and process"},
    "reload_button": {"es": "Reprocesar el archivo del servidor", "en": "Reprocess the server file"},
    "processing": {"es": "Procesando… puede tardar un par de minutos.", "en": "Processing… this can take a couple of minutes."},
    "load_done": {"es": "Datos cargados: {n} ingresos. Fecha de referencia {d}.", "en": "Data loaded: {n} admissions. Reference date {d}."},
    "current_dataset": {"es": "Datos actuales", "en": "Current dataset"},
    "quality": {"es": "Calidad de datos", "en": "Data quality"},
    # Users
    "users_intro": {"es": "Crea cuentas y define quién puede administrar el sistema.",
                    "en": "Create accounts and decide who can administer the system."},
    "create_user": {"es": "Crear usuario", "en": "Create user"},
    "full_name": {"es": "Nombre completo", "en": "Full name"},
    "role": {"es": "Rol", "en": "Role"},
    "active": {"es": "Activo", "en": "Active"},
    "edit_user": {"es": "Editar usuario", "en": "Edit user"},
    "select_user": {"es": "Usuario", "en": "User"},
    "new_password": {"es": "Nueva contraseña (opcional)", "en": "New password (optional)"},
    "save_changes": {"es": "Guardar cambios", "en": "Save changes"},
    "user_created": {"es": "Usuario creado.", "en": "User created."},
    "user_saved": {"es": "Cambios guardados.", "en": "Changes saved."},
    "password_rule": {"es": "Mínimo 10 caracteres, con letras y números o símbolos.",
                      "en": "At least 10 characters, mixing letters and numbers or symbols."},
    "last_login": {"es": "Último ingreso", "en": "Last sign-in"},
}

COLUMN_LABELS: dict[str, dict[str, str]] = {
    "_id": {"es": "Grupo", "en": "Group"},
    "bed_group": {"es": "Servicio", "en": "Service"},
    "occupied": {"es": "Ocupadas", "en": "Occupied"},
    "occupied_beds": {"es": "Camas ocupadas", "en": "Occupied beds"},
    "beds": {"es": "Camas", "en": "Beds"},
    "occupancy_pct": {"es": "Ocupación %", "en": "Occupancy %"},
    "census": {"es": "Pacientes en cama", "en": "Patients in bed"},
    "date": {"es": "Fecha", "en": "Date"},
    "name": {"es": "Nombre", "en": "Name"},
    "code": {"es": "Código", "en": "Code"},
    "medication": {"es": "Medicamento", "en": "Medication"},
    "quantity": {"es": "Cantidad", "en": "Quantity"},
    "stock": {"es": "Stock", "en": "Stock"},
    "avg_daily_consumption": {"es": "Consumo diario", "en": "Daily use"},
    "days_of_inventory": {"es": "Días de inventario", "en": "Days of inventory"},
    "reorder_point": {"es": "Punto de reorden", "en": "Reorder point"},
    "avg_wait_minutes": {"es": "Espera promedio (min)", "en": "Average wait (min)"},
    "mean_minutes": {"es": "Espera promedio (min)", "en": "Average wait (min)"},
    "median_minutes": {"es": "Mediana (min)", "en": "Median (min)"},
    "patients": {"es": "Pacientes", "en": "Patients"},
    "admissions": {"es": "Ingresos", "en": "Admissions"},
    "services": {"es": "Servicios prestados", "en": "Services provided"},
    "specialty": {"es": "Especialidad", "en": "Specialty"},
    "area": {"es": "Área", "en": "Area"},
    "triage_level": {"es": "Nivel de triage", "en": "Triage level"},
    "shift": {"es": "Turno", "en": "Shift"},
    "scheduled": {"es": "Programadas", "en": "Scheduled"},
    "performed": {"es": "Realizadas", "en": "Performed"},
    "completion_pct": {"es": "Cumplimiento %", "en": "Completion %"},
    "surgeries_scheduled": {"es": "Programadas", "en": "Scheduled"},
    "surgeries_performed": {"es": "Realizadas", "en": "Performed"},
    "month": {"es": "Mes", "en": "Month"},
    "admission_id": {"es": "Ingreso", "en": "Admission"},
    "admission_date": {"es": "Fecha de ingreso", "en": "Admission date"},
    "admission_route": {"es": "Vía de ingreso", "en": "Admission route"},
    "sex": {"es": "Sexo", "en": "Sex"},
    "age_group": {"es": "Grupo de edad", "en": "Age group"},
    "diagnosis_chapter": {"es": "Grupo diagnóstico", "en": "Diagnosis group"},
    "primary_specialty": {"es": "Especialidad principal", "en": "Main specialty"},
    "length_of_stay_days": {"es": "Estancia (días)", "en": "Length of stay (days)"},
    "email": {"es": "Correo", "en": "Email"},
    "full_name": {"es": "Nombre", "en": "Name"},
    "role": {"es": "Rol", "en": "Role"},
    "is_active": {"es": "Activo", "en": "Active"},
    "created_at": {"es": "Creado", "en": "Created"},
    "last_login_at": {"es": "Último ingreso", "en": "Last sign-in"},
}

CHAPTER_LABELS: dict[str, dict[str, str]] = {
    "infectious": {"es": "Infecciosas", "en": "Infectious"}, "neoplasms": {"es": "Tumores", "en": "Neoplasms"},
    "blood": {"es": "Sangre", "en": "Blood"}, "endocrine": {"es": "Endocrinas", "en": "Endocrine"},
    "mental": {"es": "Mentales", "en": "Mental"}, "nervous": {"es": "Sistema nervioso", "en": "Nervous system"},
    "eye": {"es": "Ojo", "en": "Eye"}, "ear": {"es": "Oído", "en": "Ear"},
    "circulatory": {"es": "Circulatorio", "en": "Circulatory"}, "respiratory": {"es": "Respiratorio", "en": "Respiratory"},
    "digestive": {"es": "Digestivo", "en": "Digestive"}, "skin": {"es": "Piel", "en": "Skin"},
    "musculoskeletal": {"es": "Osteomuscular", "en": "Musculoskeletal"},
    "genitourinary": {"es": "Genitourinario", "en": "Genitourinary"},
    "pregnancy": {"es": "Embarazo y parto", "en": "Pregnancy"}, "perinatal": {"es": "Perinatal", "en": "Perinatal"},
    "congenital": {"es": "Congénitas", "en": "Congenital"}, "symptoms": {"es": "Síntomas y signos", "en": "Symptoms"},
    "injury": {"es": "Traumatismos", "en": "Injuries"}, "external": {"es": "Causas externas", "en": "External causes"},
    "health_factors": {"es": "Factores de salud", "en": "Health factors"}, "special": {"es": "Especiales", "en": "Special"},
}

VALUE_LABELS: dict[str, dict[str, str]] = {
    "day": {"es": "Día (7–19 h)", "en": "Day (7am–7pm)"},
    "night": {"es": "Noche (19–7 h)", "en": "Night (7pm–7am)"},
    "admin": {"es": "Administrador", "en": "Administrator"},
    "user": {"es": "Usuario", "en": "User"},
}


def lang() -> str:
    return st.session_state.get("lang", "es")


def t(key: str, **params) -> str:
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(lang(), entry["es"])
    return text.format(**params) if params else text


def col_label(column: str) -> str:
    entry = COLUMN_LABELS.get(column)
    return entry.get(lang(), column) if entry else column.replace("_", " ").capitalize()


def value_label(value) -> str:
    if isinstance(value, str):
        if value in VALUE_LABELS:
            return VALUE_LABELS[value][lang()]
        if value in CHAPTER_LABELS:
            return CHAPTER_LABELS[value][lang()]
    return value


def fmt_num(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{float(value):,.{digits}f}"
    if lang() == "es":
        text = text.replace(",", "§").replace(".", ",").replace("§", ".")
    return text
