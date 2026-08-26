"""Aplicación web en Streamlit."""
import streamlit as st
import os
import requests


API_URL_PREDICCION = "http://127.0.0.1:8000/api/v1/prediccion"
API_URL_EDA = "http://127.0.0.1:8000/api/v1/eda"
#API_URL_EC2 = os.environ.get("API_BASE_URL")

st.set_page_config(
    page_title="APP Nubes y Claros",
    page_icon="🌤️",
    layout="wide",
)

st.title("🌤️ Proyecto predicción de temperatura dai08rt de Hack a BOSS")

st.write(
    """
    Bienvenido a la APP Nubes y Claros.\n
    Esta aplicación permite obtener una predicción de la
    **temperatura media de mañana** para una localidad de España.
    Introduce el nombre del municipio que quieres consultar.
    """
)

municipio = st.text_input(
    "Municipio:",
    placeholder="Por ejemplo: Zuera, provincia de Zaragoza",
)

if st.button("Consultar predicción"):
    if not municipio.strip():
        st.warning("Introduce el nombre de un municipio.")
    else:
        payload = {
            "municipio": municipio
        }
        try:
            response = requests.post(
                API_URL_PREDICCION,
                json=payload,
                timeout=60,
            )
            if response.status_code == 200:
                datos = response.json()
                fecha = datos["fecha"]
                municipio_respuesta = datos["municipio"]
                provincia = datos["provincia"]
                temperatura = datos["temperatura_ponderada"]
                st.success(
                    f"La temperatura media para el día **{fecha}** en el municipio de "
                    f"**{municipio_respuesta}**, provincia de **{provincia}**, "
                    f"es de **{temperatura} ºC**."
                )

                

            elif response.status_code == 404:
                st.error(
                    "No se ha encontrado una predicción disponible para ese municipio.\n\n"
                    "Por favor, tenga en cuenta que el municipio debe pertener al reino de España."
                )
            else:
                st.error(f"Error al consultar la API: {response.status_code}")

        except requests.exceptions.ConnectionError:

            st.error("No se ha podido conectar con la API. Comprueba que FastAPI está ejecutándose.")

        except requests.exceptions.Timeout:

            st.error("La consulta ha tardado demasiado en responder. Pruebe de nuevo más tarde")

        except Exception as e:
            st.error(f"Se ha producido un error: {e}")

st.divider()

st.subheader("📊 Histórico de temperaturas")

st.write(
    """
    En esta sección puede consultar los datos históricos de temperatura media diaria en un intervalo de tiempo contínuo.\n 
    El intervalo tiene que ser desde los últimos 10 años y hasta hace 5 días.
    """
)

consulta_eda = st.text_input(
    "Consulta histórica:",
    placeholder="Por ejemplo: Temperatura media de Barajas desde marzo a junio de 2020",
)

if st.button("Consultar perido"):

    if not consulta_eda.strip():
        st.warning(
            "Introduce una consulta histórica."
        )

    else:
        payload = {
            "consulta": consulta_eda
        }

        try:
            response = requests.post(
                API_URL_EDA,
                json=payload,
                timeout=60,
            )

            if response.status_code == 200:

                datos = response.json()

                municipio = datos["municipio"]
                provincia = datos["provincia"]
                fecha_inicio = datos["fecha_inicio"]
                fecha_fin = datos["fecha_fin"]
                temperaturas = datos["datos"]

                st.success(
                    f"Histórico de temperatura media de "
                    f"**{municipio}**, provincia de **{provincia}**, "
                    f"desde **{fecha_inicio}** hasta **{fecha_fin}**."
                )

                if temperaturas:
                    import pandas as pd
                    df_temperaturas = pd.DataFrame(
                        temperaturas
                    )
                    df_temperaturas["fecha"] = pd.to_datetime(
                        df_temperaturas["fecha"]
                    )
                    temperatura_max = df_temperaturas["temperatura_media"].max()
                    temperatura_media = df_temperaturas["temperatura_media"].mean()
                    temperatura_min = df_temperaturas["temperatura_media"].min()
                    fila_max = df_temperaturas.loc[df_temperaturas["temperatura_media"].idxmax()]
                    fila_min = df_temperaturas.loc[df_temperaturas["temperatura_media"].idxmin()]
                    fecha_max = fila_max["fecha"].strftime("%d/%m/%Y")
                    fecha_min = fila_min["fecha"].strftime("%d/%m/%Y")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric(
                            label="Temperatura máxima",
                            value=f"{temperatura_max:.1f} ºC",
                        )

                        st.caption(f"Registrada el {fecha_max}")

                    with col2:
                        st.metric(
                            label="Temperatura media",
                            value=f"{temperatura_media:.1f} ºC",
                        )

                        st.caption("Media del periodo seleccionado")

                    with col3:

                        st.metric(
                            label="Temperatura mínima",
                            value=f"{temperatura_min:.1f} ºC",
                        )

                        st.caption(f"Registrada el {fecha_min}")

                    st.line_chart(
                        df_temperaturas,
                        x="fecha",
                        y="temperatura_media",
                    )
                    df_temperaturas["fecha"] = df_temperaturas["fecha"].dt.strftime("%d/%m/%Y")
                    st.dataframe(
                        df_temperaturas,
                        use_container_width=True,
                    )

                else:
                    st.warning(
                        "No se han encontrado temperaturas"
                        "para el periodo solicitado."
                    )

            elif response.status_code == 400:
                error = response.json()
                st.error(
                    error.get(
                        "detail",
                        "La consulta no es válida."
                    )
                )

            else:
                st.error(
                    f"Error al consultar la API: "
                    f"{response.status_code}\n\n"
                    f"{response.text}"
                )

        except requests.exceptions.ConnectionError:

            st.error(
                "No se ha podido conectar con la API. "
                "Comprueba que FastAPI está ejecutándose."
            )

        except requests.exceptions.Timeout:

            st.error("La consulta ha tardado demasiado en responder.")

        except Exception as e:

            st.error(
                f"Se ha producido un error: {e}"
            )