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
