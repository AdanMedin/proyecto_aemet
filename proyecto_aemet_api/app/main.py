from fastapi import FastAPI

app = FastAPI(title="Mi API Base", version="1.0")

@app.get("/")
def inicio():
    return {"mensaje": "¡Hola! La API funciona bien."}
