"""Aplicación web en Streamlit."""
import streamlit as st
import os
import requests


API_URL = "http://127.0.0.1:8000/api/v1/prediccion"
#API_URL_EC2 = os.environ.get("API_BASE_URL")

st.set_page_config(
    page_title="Predicción meteorológica",
    page_icon="🌤️",
)

st.title("🌤️ Predicción de temperatura")

st.write(
    """
    Bienvenido. Esta aplicación permite obtener una predicción de la
    **temperatura media de mañana** para una localidad de España.
    Introduce el nombre del municipio que quieres consultar.
    """
)

municipio = st.text_input(
    "Municipio",
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
                API_URL,
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

                st.write("¿Desea realizar otra consulta?")

            elif response.status_code == 404:
                st.error(
                    "No se ha encontrado una predicción disponible para ese municipio.\n\n"
                    "Por favor, tenga en cuenta que el municipio debe pertener al reino de España"
                )
            else:
                st.error(f"Error al consultar la API: {response.status_code}")

        except requests.exceptions.ConnectionError:

            st.error("No se ha podido conectar con la API. Comprueba que FastAPI está ejecutándose.")

        except requests.exceptions.Timeout:

            st.error("La consulta ha tardado demasiado en responder. Pruebe de nuevo más tarde")

        except Exception as e:
            st.error(f"Se ha producido un error: {e}")