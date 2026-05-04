import argparse
from .calibration import main


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=1, help="웹캠 인덱스 (기본 1 = 외장캠)")
    args = parser.parse_args()
    raise SystemExit(main(camera_index=args.camera))
