Drop the YuNet face model here (not committed, ~230 KB, MIT):

    curl -L -o face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

With it present, the `face` detector uses YuNet; without it, Haar cascades.
