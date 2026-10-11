"""書き出した PDF を可逆に詰め直す（オブジェクトストリーム化 + ストリーム圧縮）。

pypdf は元 PDF のオブジェクトストリーム（小さなオブジェクトをまとめて圧縮した領域）を展開して
書き出すため、注釈・タグ・図形が多い PDF は結合や保存で数倍に膨らむ（720KB×4 → 4.9MB を確認）。
qpdf ベースの pikepdf で詰め直すと元の大きさに戻る。画像などの中身は変えない。
詰め直せない（pikepdf が無い・失敗した）ときや、小さくならないときは何もしない。
"""

from __future__ import annotations

from pathlib import Path


def repack_in_place(path: Path | str) -> bool:
    """path の PDF を詰め直して、小さくなったときだけ置き換える。置き換えたら True。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".pack")
    try:
        import pikepdf

        with pikepdf.open(path) as pdf:
            pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.generate, compress_streams=True)
        if tmp.stat().st_size < path.stat().st_size:
            tmp.replace(path)
            return True
        return False
    except Exception:  # noqa: BLE001 — 詰め直しは最適化でしかないので、失敗しても元の出力を使う
        return False
    finally:
        tmp.unlink(missing_ok=True)
