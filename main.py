from fastapi import FastAPI, HTTPException
import pandapower as pp
from pydantic import BaseModel
from typing import List, Dict
import os
import uvicorn

app = FastAPI(
    title="Motor de Flujo de Potencia Dinámico - Red Eléctrica",
    version="5.0.0"
)

class TramoTopologia(BaseModel) :
    origen: str
    destino: str
    longitud_km: float

class CargaBus(BaseModel):
    nodo_id: str
    p_mw: float
    q_mvar: float

class AnalisisDinamicoParams(BaseModel):
    temperatura_ambiente: float = 25.0
    factor_proyeccion: float = 1.0
    nodos: List[str]                  # Lista de IDs de nodos (ej: ["N01", "N02", ...])
    nombres_nodos: Dict[str, str]     # Diccionario de nombres
    tramos: List[TramoTopologia]      # Lista de líneas (origen -> destino)
    cargas: List[CargaBus]            # Cargas conectadas por nodo

@app.post("/api/analisis/dinamico")
def ejecutar_flujo_dinamico(params: AnalisisDinamicoParams):
    net = pp.create_empty_network(f_hz=50.0)
    
    # Parámetros térmicos conductor ACSR 4/0
    r_20 = 0.548
    alpha = 0.00403
    r_ajustada = r_20 * (1 + alpha * (params.temperatura_ambiente - 20.0))
    x_km = 0.410
    c_km = 9.5
    max_i_ka = 0.260

    # 1. Crear Buses
    buses_dict = {}
    for nid in params.nodos:
        nombre = params.nombres_nodos.get(nid, nid)
        buses_dict[nid] = pp.create_bus(net, vn_kv=24.9, name=nombre)

    # 2. Subestación Slack en el primer nodo (ej: N01)
    if params.nodos:
        pp.create_ext_grid(net, bus=buses_dict[params.nodos[0]], vm_pu=1.0, mva_base=100.0, name="Subestación Principal Slack")

    # 3. Crear Líneas (Soporta Radial o Mallado según los tramos enviados)
    for tramo in params.tramos:
        if tramo.origen in buses_dict and tramo.destino in buses_dict:
            pp.create_line_from_parameters(
                net,
                from_bus=buses_dict[tramo.origen],
                to_bus=buses_dict[tramo.destino],
                length_km=max(tramo.longitud_km, 0.1),
                r_ohm_per_km=r_ajustada,
                x_ohm_per_km=x_km,
                c_nf_per_km=c_km,
                max_i_ka=max_i_ka,
                name=f"Linea_{tramo.origen}_{tramo.destino}"
            )

    # 4. Crear Cargas
    for carga in params.cargas:
        if carga.nodo_id in buses_dict and carga.nodo_id != params.nodos[0]:
            pp.create_load(
                net, 
                bus=buses_dict[carga.nodo_id], 
                p_mw=carga.p_mw * params.factor_proyeccion, 
                q_mvar=carga.q_mvar * params.factor_proyeccion, 
                name=f"Carga_{carga.nodo_id}"
            )

    # --- Ejecutar Flujo de Potencia ---
    try:
        pp.runpp(net, algorithm='nr')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fallo de convergencia en el Flujo de Carga: {str(e)}")

    perdidas_kw = float(net.res_line["pl_mw"].sum() * 1000)
    min_voltaje_pu = float(net.res_bus["vm_pu"].min())
    max_cargabilidad_pct = float(net.res_line["loading_percent"].max())

    # Resultados detallados por barra
    resultados_buses = []
    for idx in net.bus.index:
        resultados_buses.append({
            "indice_bus": int(idx),
            "nombre_bus": str(net.bus.at[idx, "name"]),
            "voltaje_pu": round(float(net.res_bus.at[idx, "vm_pu"]), 4),
            "angulo_grados": round(float(net.res_bus.at[idx, "va_degree"]), 2),
            "potencia_activa_mw": round(float(net.res_bus.at[idx, "p_mw"]), 4),
            "potencia_reactiva_mvar": round(float(net.res_bus.at[idx, "q_mvar"]), 4)
        })

    return {
        "estado": "Flujo dinámico ejecutado con éxito",
        "resultados_flujo_y_perdidas": {
            "perdidas_tecnicas_totales_kw": round(perdidas_kw, 2),
            "voltaje_minimo_red_pu": round(min_voltaje_pu, 4),
            "maxima_cargabilidad_linea_pct": round(max_cargabilidad_pct, 2)
        },
        "parametros_por_bus": resultados_buses
    }

@app.get("/")
def leer_raiz():
    return {"sistema": "Motor Dinámico de Flujo de Potencia", "estado": "Activo"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)


