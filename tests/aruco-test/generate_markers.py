import cv2
import os

SAVE_DIR = "markers"
os.makedirs(SAVE_DIR, exist_ok=True)

aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

# 4x4 비트 + 검은 테두리 = 6칸. 검은 건반 몸체에 붙여도 테두리가 묻히지
# 않도록 흰 여백 1칸을 이미지에 넣어 둔다(여백까지 잘라 붙이면 된다).
MARKER_SIZE = 420
QUIET_ZONE = MARKER_SIZE // 6

for marker_id in range(4):
    marker = cv2.aruco.generateImageMarker(
        aruco_dict,
        marker_id,
        MARKER_SIZE
    )
    marker = cv2.copyMakeBorder(
        marker,
        QUIET_ZONE, QUIET_ZONE, QUIET_ZONE, QUIET_ZONE,
        cv2.BORDER_CONSTANT,
        value=255
    )

    filename = os.path.join(SAVE_DIR, f"marker_{marker_id}.png")
    cv2.imwrite(filename, marker)
    print(f"Saved: {filename}")
