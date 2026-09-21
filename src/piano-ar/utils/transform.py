import cv2
import numpy as np

WIDTH = 800
HEIGHT = 200

def order_points(pts):
    pts = np.array(pts, dtype="float32")

    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)

    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]

    return np.array([tl, tr, br, bl], dtype="float32")


def get_matrix(points):
    src = order_points(points)

    dst = np.float32([
        [0, 0],
        [WIDTH, 0],
        [WIDTH, HEIGHT],
        [0, HEIGHT]
    ])

    return cv2.getPerspectiveTransform(src, dst)


def warp(frame, matrix):
    return cv2.warpPerspective(frame, matrix, (WIDTH, HEIGHT))


def rotate_points_180(points, size):
    w, h = size
    pts = np.asarray(points, dtype=np.float32)
    return np.stack([w - 1 - pts[:, 0], h - 1 - pts[:, 1]], axis=1)


# 워프 공간(800x200) → 원본 카메라 좌표
def unwarp_points(points, matrix):
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.linalg.inv(matrix)).reshape(-1, 2)


def unwarp(image, matrix, size, interpolation=cv2.INTER_LINEAR):
    # matrix 는 원본 → 워프 방향이므로, 역매핑 플래그로 그대로 되돌린다.
    return cv2.warpPerspective(image, matrix, size,
                               flags=interpolation | cv2.WARP_INVERSE_MAP)
