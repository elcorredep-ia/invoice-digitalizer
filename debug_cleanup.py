from services.ai_service import _cleanup_name

value = 'DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V..'
print(repr(value))
print(repr(_cleanup_name(value)))
