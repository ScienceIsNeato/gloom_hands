The YuNet face model lives here. It is not in git (binary, and everyone can
fetch their own), so a fresh clone needs it once.

    curl -fL -o face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

**Use `-f`.** Without it, curl happily saves GitHub's 404 page over the
filename you asked for, and a 404 page is about 260 KB, which looks like a
plausible download until OpenCV refuses to parse it.

Then check what you got:

    macOS / Linux   shasum -a 256 face_detection_yunet_2023mar.onnx
    Windows         certutil -hashfile face_detection_yunet_2023mar.onnx SHA256

    232589 bytes
    8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4

With this file present the `face` detector is YuNet. Without it, or with a
broken copy, it says so and falls back to the old Haar cascades, which are
far worse at finding a real face.

Pinned to the 2023mar model on purpose: it has a fixed input shape, which is
what opencv-python 4.x wants. The newer 2026may model targets the OpenCV 5.x
ONNX engine and will not load here.
