# -*- coding: utf-8 -*-
"""
置頂腳本元件（Frontend — Scroll Script）
僅放置以 st.components.v1.html() 封裝的切換股票強制置頂 JavaScript 腳本。
"""

import streamlit as st


def force_scroll_to_top():
    """標記需要強制置頂，由 render_scroll_script 注入 MutationObserver 滾動重置。"""
    st.session_state["scroll_to_top"] = True


def render_scroll_script():
    """注入 JavaScript：股票切換後強制置頂 MutationObserver + 多時段延遲。

    僅在 session_state["scroll_to_top"] 為 True 時執行，執行後自動清除旗標。
    """
    if not st.session_state.pop("scroll_to_top", False):
        return

    st.components.v1.html(
        """
<script>
    (function keepMainAtTop() {
        var doc = window.parent.document;

        function forceScrollTop() {
            window.parent.scrollTo(0, 0);
            doc.documentElement.scrollTop = 0;
            doc.body.scrollTop = 0;

            var mainEl = doc.querySelector('section.main');
            if (mainEl) mainEl.scrollTop = 0;

            var blockContainer = doc.querySelector('[data-testid="stMainBlockContainer"]') || doc.querySelector('.block-container');
            if (blockContainer) blockContainer.scrollTop = 0;
        }

        forceScrollTop();
        [50, 150, 300, 600, 1000, 1500].forEach(function(delay) {
            setTimeout(forceScrollTop, delay);
        });

        var observer = new MutationObserver(function(mutations) {
            forceScrollTop();
        });

        var targetNode = doc.querySelector('section.main') || doc.body;
        if (targetNode) {
            observer.observe(targetNode, { childList: true, subtree: true });
            setTimeout(function() {
                observer.disconnect();
            }, 2500);
        }
    })();
</script>
""",
        height=0,
        width=0,
    )
