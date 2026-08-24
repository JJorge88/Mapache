# Modelos faciales de Mapache AI

Archivos esperados:

- `face_detection_yunet_2023mar.onnx`
  - fuente: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
  - SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
  - licencia declarada por el directorio: MIT
- `face_recognition_sface_2021dec.onnx`
  - fuente: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
  - SHA-256: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`
  - licencia declarada por el directorio: Apache-2.0

YuNet detecta rostros y landmarks. SFace alinea cada detección y produce el descriptor de
128 dimensiones usado por pgvector con distancia coseno.

Antes de uso comercial debe confirmarse la licencia y procedencia del peso SFace exacto,
incluyendo las condiciones de los datasets de entrenamiento. El directorio declara
Apache-2.0, pero esa declaración por sí sola no resuelve todas las preguntas sobre datos
de entrenamiento. Conservar las licencias y atribuciones de las fuentes oficiales.
