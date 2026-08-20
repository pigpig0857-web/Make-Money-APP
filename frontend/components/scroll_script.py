import streamlit as st
import streamlit.components.v1 as components

def render_scroll_script():
    # 遍歷 DOM 中所有可能具備 overflow 滾動軸的容器，並一律強制歸零
    components.html(
        """
        <script>
            (function() {
                var parentDoc = window.parent.document;
                
                function resetAllScrolls() {
                    try {
                        // 1. 強制重置最外層 window
                        window.parent.scrollTo(0, 0);

                        // 2. 抓取所有可能產生滾動軸的 Streamlit 容器
                        var selectors = [
                            'section.main',
                            '[data-testid="stMain"]',
                            '[data-testid="stMainBlockContainer"]',
                            '.main',
                            '.block-container'
                        ];

                        selectors.forEach(function(sel) {
                            var els = parentDoc.querySelectorAll(sel);
                            els.forEach(function(el) {
                                if (el) {
                                    el.scrollTop = 0;
                                    el.scrollTo({ top: 0, left: 0, behavior: 'instant' });
                                }
                            });
                        });
                    } catch(e) {
                        console.error('Scroll reset error:', e);
                    }
                }

                // 綁定左側 Sidebar 點擊事件，觸發連續重置
                if (!window.parent._globalScrollFixAdded) {
                    window.parent._globalScrollFixAdded = true;
                    parentDoc.addEventListener('click', function(e) {
                        var sidebar = parentDoc.querySelector('section[data-testid="stSidebar"]');
                        if (sidebar && sidebar.contains(e.target)) {
                            var count = 0;
                            var timer = setInterval(function() {
                                resetAllScrolls();
                                count++;
                                if (count > 25) clearInterval(timer); // 連續狂刷新 1.2 秒
                            }, 40);
                        }
                    }, true);
                }

                resetAllScrolls();
            })();
        </script>
        """,
        height=0,
        width=0
    )

def force_scroll_to_top():
    render_scroll_script()
