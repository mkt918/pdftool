"""ビューアの描画待ち行列: 1 件ずつ、今のページ優先、通り過ぎたページは捨てる、失敗しても止まらない。"""

from pathlib import Path

from PySide6.QtGui import QImage

from pdftool.core.pageops import PageRef
from pdftool.ui.viewer import ViewerWidget


class FakeRenderer:
    """要求を貯めておき、テストが 1 件ずつ完了させる。"""

    def __init__(self):
        self.calls: list[tuple[PageRef, tuple]] = []
        self.pending: list[tuple[PageRef, object]] = []

    def __call__(self, ref, cb, *dpi):
        self.calls.append((ref, dpi))
        self.pending.append((ref, cb))

    def finish(self, ok: bool = True):
        ref, cb = self.pending.pop(0)
        cb(QImage(40, 56, QImage.Format.Format_RGB888) if ok else None)
        return ref


def _viewer(app, n=12):
    r = FakeRenderer()
    vw = ViewerWidget(r)
    vw.set_spread(False)
    refs = [PageRef(Path("x.pdf"), i) for i in range(n)]
    vw.set_refs(refs, 0)
    return vw, r, refs


def test_one_request_at_a_time_current_page_first(app):
    vw, r, refs = _viewer(app)
    assert [c[0] for c in r.calls] == [refs[0]]            # 一度に出すのは 1 件
    r.finish()
    assert vw.right._image is not None
    # 続いて先読み（次の 2 ページ）を 1 件ずつ
    assert r.calls[-1][0] == refs[1]
    r.finish()
    assert r.calls[-1][0] == refs[2]


def test_quick_flipping_skips_pages_passed_by(app):
    vw, r, refs = _viewer(app)
    for _ in range(8):                                     # 1 ページ目の描画中に 8 ページめくる
        vw.step(1)
    r.finish()                                             # 描画中だった 1 ページ目が終わる
    assert r.calls[-1][0] == refs[8]                       # 次は今のページ（途中のページは描かない）
    r.finish()
    assert vw.right.ref == refs[8] and vw.right._image is not None
    asked = [c[0] for c in r.calls]
    assert refs[3] not in asked and refs[5] not in asked


def test_failed_render_does_not_stall_the_queue(app):
    vw, r, refs = _viewer(app)
    r.finish(ok=False)                                     # 1 ページ目が描けなかった
    assert vw.right._failed                                # 「表示できません」と出す
    assert r.pending and r.calls[-1][0] == refs[1]         # 止まらずに次へ
    n = len(r.calls)
    vw.go(0)
    assert all(c[0] != refs[0] for c in r.calls[n:])       # 失敗したページは何度も試さない
