"""Semantic, high-contrast Qt theme system for 科研助手.

Raw colour values belong only in this module. The rest of the interface uses
object names (``#paperCard``, ``#primaryButton`` …), so a saved theme can be
swapped without changing individual pages or reintroducing unreadable popup
menus.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Final

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QWidget


THEME_REGISTRY: Final[dict[str, dict[str, str]]] = {
    "fog_teal": {
        "name": "雾青蓝", "window": "#EAF2F2", "surface": "#F8FBFA", "text": "#173337",
        "accent": "#246F79", "success": "#3B7560", "warning": "#A86D31", "danger": "#AA514D",
        "muted": "#61777A", "border": "#B8CBC8", "field": "#FFFFFF", "raised": "#E2ECEA",
        "hover": "#D7E7E5", "selection": "#C6E2DF", "on_accent": "#FFFFFF", "dark": "false",
    },
}

SECTION_ACCENTS: Final[dict[str, str]] = {
    "home": "#246F79",
    "frontier": "#4A78D0",
    "journals": "#2F8B62",
    "special_issues": "#D77A32",
    "papers": "#7656B6",
    "achievements": "#278C93",
    "todo": "#B88318",
    "notes": "#C05B83",
}


_BUNDLED_CJK_FONT_FAMILY: Final[str] = "Noto Sans CJK SC"
_BUNDLED_CJK_FONT_RELATIVE_PATH: Final[Path] = Path("assets") / "fonts" / "NotoSansCJKsc-Regular.otf"


def _bundled_cjk_font_path() -> Path:
    """Locate the shipped Chinese fallback both from source and PyInstaller."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        bundle_root = Path(__file__).resolve().parents[1]
    return bundle_root / _BUNDLED_CJK_FONT_RELATIVE_PATH


def ensure_application_font(application: QApplication) -> str:
    """Register the bundled CJK font once so a bare Windows profile is readable."""
    loaded_family = str(application.property("research_assistant_font_family") or "")
    if loaded_family:
        return loaded_family

    font_path = _bundled_cjk_font_path()
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if not families:
        return ""

    family = _BUNDLED_CJK_FONT_FAMILY if _BUNDLED_CJK_FONT_FAMILY in families else families[0]
    application.setFont(QFont(family))
    application.setProperty("research_assistant_font_family", family)
    return family


def get_theme(theme_id: str | None) -> dict[str, str]:
    """Return a copy so consumers cannot mutate the registry."""
    del theme_id
    return dict(THEME_REGISTRY["fog_teal"])


def theme_choices() -> list[tuple[str, str]]:
    return [(theme_id, spec["name"]) for theme_id, spec in THEME_REGISTRY.items()]


def normalize_density(value: str | None) -> str:
    return "compact" if str(value).strip().casefold() == "compact" else "comfortable"


def _theme_id(theme_id: str | None) -> str:
    del theme_id
    return "fog_teal"


def build_application_stylesheet(theme_id: str | None = "fog_teal", density: str | None = "comfortable") -> str:
    """Build the stable semantic QSS used by every palette.

    ``theme_id`` stays in the public signature for compatibility, but the
    colour tokens intentionally come from ``QPalette``.  Replacing a global
    stylesheet makes Qt re-polish every child widget, so ordinary Settings
    saves must not do it.  A genuine theme or density change refreshes the QSS
    once, which also clears Qt's cached ``palette(...)`` lookups on visible
    cards.
    """
    compact = normalize_density(density) == "compact"
    base_size = 11 if compact else 12
    title_size = 16 if compact else 18
    card_title_size = 11 if compact else 12
    spacing = 5 if compact else 7
    return f"""
        QWidget {{
            color: palette(window-text);
            font-size: {base_size}px;
        }}
        QMainWindow, QDialog {{ background: palette(window); }}
        #windowRoot {{ background: palette(window); border: 1px solid palette(shadow); border-radius: 10px; }}
        #contentStack, #settingsContent {{ background: palette(window); }}
        #sidebar {{ background: palette(button); border-right: 1px solid palette(shadow); }}
        #topbar {{ background: palette(alternate-base); border-bottom: 1px solid palette(shadow); }}
        #brand, #settingsTitle {{ color: palette(window-text); font-size: 15px; font-weight: 700; letter-spacing: 1px; }}
        #settingsTitle {{ font-size: {title_size}px; letter-spacing: 0; }}
        #dialogTitle {{ color: palette(window-text); font-size: {title_size}px; font-weight: 700; }}
        #dialogSubtitle {{ color: palette(mid); }}
        #topHint, #toolbarLabel, #settingsHint, #settingsPath, #settingsSectionHint,
        #cardHint, #sectionLabel, #formHint, #dateLabel, #cardDetail, #paperLifecycleHint,
        #achievementMeta, #rejectionArchiveMeta, #archiveHint, #frontierMatches {{ color: palette(mid); }}
        #pageTitle {{ color: palette(window-text); font-size: {title_size + 2}px; font-weight: 700; }}
        #paperPageTitle {{ color: palette(window-text); font-size: {title_size}px; font-weight: 700; }}
        #summaryLabel, #metricValue, #cardTitle, #journalCopyName, #frontierTitle, #achievementTitle, #noteItemTitle,
        #readingTitle, #rejectionArchiveText, #settingsSectionTitle {{ color: palette(window-text); font-weight: 700; }}
        #cardTitle, #journalCopyName, #frontierTitle, #achievementTitle, #noteItemTitle, #readingTitle {{ font-size: {card_title_size}px; }}

        QPushButton, QToolButton {{
            background: palette(button); color: palette(button-text); border: 1px solid palette(shadow);
            border-radius: 7px; padding: {spacing}px {spacing + 3}px;
        }}
        QPushButton:hover, QToolButton:hover {{ background: palette(light); border-color: palette(highlight); }}
        QPushButton:disabled, QToolButton:disabled {{ color: palette(mid); background: palette(window); border-color: palette(shadow); }}
        #primaryButton, #restoreButton, #futureDateRepairButton {{
            background: palette(link); color: palette(base); border-color: palette(link); font-weight: 700;
        }}
        #primaryButton:hover, #restoreButton:hover {{ background: palette(highlight); border-color: palette(highlight); color: palette(highlighted-text); }}
        #primaryAction, #fileOpenButton {{ background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); font-weight: 700; }}
        #primaryAction:hover, #fileOpenButton:hover {{ background: palette(link); border-color: palette(link); }}
        #nextActionButton {{ text-align: left; min-height: 24px; padding: 4px {spacing + 2}px; }}
        #subtleButton, #iconButton, #windowButton, #lockButton, #rowButton, #dragHandle {{ background: transparent; border-color: transparent; }}
        #rowButton {{ color: palette(mid); padding: 2px 4px; min-height: 18px; }}
        #dragHandle {{ color: palette(mid); font-size: 15px; padding: 0; min-width: 22px; }}
        #subtleButton:hover, #iconButton:hover, #windowButton:hover, #lockButton:hover, #rowButton:hover, #dragHandle:hover {{ background: palette(light); color: palette(window-text); }}
        #archiveCornerButton {{ color: palette(link-visited); padding: 4px 7px; }}
        #closeButton, #dangerButton {{ background: transparent; color: palette(link-visited); border-color: transparent; }}
        #closeButton:hover, #dangerButton:hover {{ background: palette(link-visited); color: palette(base); border-color: palette(link-visited); }}
        #navButton {{ background: transparent; color: palette(mid); border: 0; border-radius: 7px; padding: 7px 6px; font-size: 11px; text-align: left; }}
        #navButton:hover {{ background: palette(light); color: palette(window-text); }}
        #navButton:checked {{ background: palette(midlight); color: palette(highlight); font-weight: 700; }}
        #sidebarPinButton, #lockButton:checked {{ background: palette(midlight); border-color: palette(highlight); color: palette(highlight); }}
        #workbenchChip[accent="tasks"]:checked {{ background: #F1E2BC; color: #7A540E; border-color: #B88318; }}
        #workbenchChip[accent="notes"]:checked {{ background: #F5E0E9; color: #91415F; border-color: #C05B83; }}
        #workbenchChip[accent="submissions"]:checked {{ background: #EAE3F5; color: #5D4192; border-color: #7656B6; }}
        #workbenchChip[accent="results"]:checked {{ background: #DCEFF0; color: #1D6D72; border-color: #278C93; }}
        #workbenchChip[accent="frontier"]:checked {{ background: #E4EBF8; color: #315DAA; border-color: #4A78D0; }}
        #workbenchChip[accent="journals"]:checked {{ background: #E1F0E8; color: #226B4A; border-color: #2F8B62; }}
        #workbenchChip[accent="special_issues"]:checked {{ background: #F8E6D7; color: #A9561D; border-color: #D77A32; }}

        #pageHeader {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-left: 3px solid palette(highlight); border-radius: 8px; }}
        #pageHeaderTitle {{ color: palette(window-text); font-size: {title_size}px; font-weight: 700; }}
        #pageHeaderHint, #filterCount {{ color: palette(mid); font-size: 11px; }}
        #filterBar {{ background: transparent; }}
        #ellipsisButton {{ min-width: 28px; max-width: 28px; padding: {spacing}px 2px; font-weight: 700; }}
        #statusBadge {{ background: palette(button); color: palette(mid); border-radius: 7px; padding: 3px 6px; font-size: 10px; }}
        #statusBadge[tone="success"] {{ color: #2F8B62; background: #E5F3EC; }}
        #statusBadge[tone="info"] {{ color: #3D67B4; background: #E8EEF9; }}
        #statusBadge[tone="warning"] {{ color: #B25E24; background: #FAEBDD; }}
        #statusBadge[tone="muted"] {{ color: #61777A; background: #E7ECEB; }}

        #pageHeader[accent="frontier"], #accentCard[accent="frontier"] {{ border-left-color: #4A78D0; }}
        #pageHeader[accent="journals"], #accentCard[accent="journals"] {{ border-left-color: #2F8B62; }}
        #pageHeader[accent="special_issues"], #accentCard[accent="special_issues"] {{ border-left-color: #D77A32; }}
        #pageHeader[accent="papers"], #accentCard[accent="papers"] {{ border-left-color: #7656B6; }}
        #pageHeader[accent="achievements"], #accentCard[accent="achievements"] {{ border-left-color: #278C93; }}
        #pageHeader[accent="todo"], #accentCard[accent="todo"] {{ border-left-color: #B88318; }}
        #pageHeader[accent="notes"], #accentCard[accent="notes"] {{ border-left-color: #C05B83; }}
        #pageHeader[accent="home"], #accentCard[accent="home"] {{ border-left-color: #246F79; }}
        #accentCard {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-left: 3px solid palette(highlight); border-radius: 8px; }}
        #accentCard:hover {{ border-color: palette(highlight); }}

        #accentPrimary[accent="frontier"] {{ background: #4A78D0; border-color: #4A78D0; color: white; }}
        #accentPrimary[accent="journals"] {{ background: #2F8B62; border-color: #2F8B62; color: white; }}
        #accentPrimary[accent="special_issues"] {{ background: #D77A32; border-color: #D77A32; color: white; }}
        #accentPrimary[accent="papers"] {{ background: #7656B6; border-color: #7656B6; color: white; }}
        #accentPrimary[accent="achievements"] {{ background: #278C93; border-color: #278C93; color: white; }}
        #accentPrimary[accent="todo"] {{ background: #B88318; border-color: #B88318; color: white; }}
        #accentPrimary[accent="notes"] {{ background: #C05B83; border-color: #C05B83; color: white; }}
        #accentPrimary[accent="home"] {{ background: #246F79; border-color: #246F79; color: white; }}

        QLineEdit, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QKeySequenceEdit {{
            background: palette(base); color: palette(text); border: 1px solid palette(shadow);
            border-radius: 7px; padding: {spacing}px {spacing + 2}px; selection-background-color: palette(highlight);
        }}
        QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QKeySequenceEdit:focus {{ border: 2px solid palette(highlight); }}
        QComboBox {{ padding-right: 24px; min-height: 17px; }}
        QComboBox::drop-down {{ width: 22px; border: 0; border-left: 1px solid palette(shadow); background: palette(button); }}
        QComboBox::drop-down:hover {{ background: palette(light); }}
        QComboBox::down-arrow {{
            image: none; width: 0; height: 0; margin-right: 7px;
            border-style: solid; border-width: 5px 4px 0 4px;
            border-color: palette(mid) transparent transparent transparent;
        }}
        QComboBox QAbstractItemView, QAbstractItemView, QListView {{
            background: palette(alternate-base); color: palette(text); border: 1px solid palette(shadow);
            outline: 0; selection-background-color: palette(highlight); selection-color: palette(highlighted-text);
        }}
        QComboBox QAbstractItemView::item, QAbstractItemView::item {{ min-height: 24px; padding: 5px 9px; }}
        QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected, QAbstractItemView::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
        QMenu, #journalToolsMenu {{ background: palette(alternate-base); color: palette(text); border: 1px solid palette(shadow); padding: 4px; }}
        QMenu::item, #journalToolsMenu::item {{ padding: 6px 16px; border-radius: 5px; }}
        QMenu::item:selected, #journalToolsMenu::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
        QToolTip {{ background: palette(tooltip-base); color: palette(tooltip-text); border: 1px solid palette(shadow); padding: 5px 7px; border-radius: 5px; }}

        QCheckBox::indicator {{ width: 14px; height: 14px; background: palette(base); border: 1px solid palette(shadow); border-radius: 4px; }}
        QCheckBox::indicator:checked {{ background: palette(link); border-color: palette(link); }}
        QSlider::groove:horizontal {{ height: 4px; background: palette(shadow); border-radius: 2px; }}
        QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; border-radius: 7px; background: palette(highlight); }}

        #overviewCard, #inspirationCard, #notesCard, #paperCard, #journalLibraryRow, #frontierCard,
        #sourceCard, #journalPriorityCard, #achievementRow, #settingsSection, #readingRow, #noteItemRow,
        #journalHistory, #journalEditor, #fileAttachmentRow, #achievementPdfRow, #frontierSettingsSection,
        #profileTermCard, #profilePendingCard, #profileExcludedCard {{
            background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 9px;
        }}
        #homeFocusZone, #homeSubmissionZone, #homeKnowledgeZone, #homeDashboardCanvas {{ background: transparent; }}
        #overviewCard[homeRole="focus"] {{ background: palette(base); border: 1px solid palette(highlight); }}
        #overviewCard[homeRole="paper-rail"] {{ background: palette(alternate-base); border: 1px solid palette(shadow); }}
        #overviewCard[homeRole="next"] {{ background: palette(button); border: 1px solid palette(highlight); }}
        #overviewCard[homeRole="observation"] {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-bottom: 2px solid palette(shadow); }}
        #overviewCard[homeRole="frontier"] {{ background: palette(base); border: 1px solid palette(shadow); }}
        #overviewCard[homeRole="knowledge"] {{ background: palette(alternate-base); }}
        #overviewCard[homeRole="paper-rail"] #metricValue {{ font-size: {card_title_size}px; font-weight: 600; }}
        #overviewCard:hover, #paperCard:hover, #journalLibraryRow:hover, #frontierCard:hover, #achievementRow:hover {{ border-color: palette(highlight); background: palette(alternate-base); }}
        #journalHistory[attention="true"] {{ background: palette(light); border: 2px solid palette(highlight); }}
        #historyList {{ background: palette(alternate-base); color: palette(text); border: 1px solid palette(shadow); border-radius: 7px; }}
        #historyList::item, #todoList::item {{ border-bottom: 1px solid palette(shadow); padding: 3px; }}
        #todoList {{ background: transparent; border: 0; outline: 0; }}
        #todoText {{ color: palette(text); font-size: {card_title_size}px; font-weight: 600; }}
        #todoMeta {{ color: palette(mid); font-size: 10px; }}
        #todoPriorityButton {{ padding: 3px 6px; min-height: 18px; }}
        #todoPriorityButton[priority="urgent_important"] {{ color: palette(link-visited); border-color: palette(link-visited); }}
        #todoPriorityButton[priority="important_not_urgent"], #todoPriorityButton[priority="urgent_not_important"] {{ color: palette(bright-text); border-color: palette(bright-text); }}
        #todoPriorityButton[priority="not_urgent_not_important"] {{ color: palette(link); border-color: palette(link); }}
        #quadrantOverlay {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 9px; }}
        #quadrantOverlayTitle {{ color: palette(text); font-weight: 700; font-size: 12px; }}
        #quadrantOverlayHint {{ color: palette(mid); font-size: 10px; }}
        #quadrantRed, #quadrantYellow, #quadrantGreen {{ background: palette(button); border-radius: 7px; }}
        #quadrantRed {{ border: 1px solid palette(link-visited); }}
        #quadrantYellow {{ border: 1px solid palette(bright-text); }}
        #quadrantGreen {{ border: 1px solid palette(link); }}
        #quadrantRed:hover {{ background: palette(light); border: 2px solid palette(link-visited); }}
        #quadrantYellow:hover {{ background: palette(light); border: 2px solid palette(bright-text); }}
        #quadrantGreen:hover {{ background: palette(light); border: 2px solid palette(link); }}
        #quadrantHeading {{ font-weight: 700; font-size: 11px; }}
        #quadrantRed #quadrantHeading {{ color: palette(link-visited); }}
        #quadrantYellow #quadrantHeading {{ color: palette(bright-text); }}
        #quadrantGreen #quadrantHeading {{ color: palette(link); }}
        #quadrantHint {{ color: palette(mid); font-size: 10px; }}
        #doneText {{ color: palette(mid); text-decoration: line-through; }}
        #todoDot, #todoYellowDot, #priorityYellowTag, #nodeYellow, #frontierMust {{ color: palette(bright-text); }}
        #todoRedDot, #priorityRedTag, #nodeRed, #dangerMarker {{ color: palette(link-visited); }}
        #todoGreenDot, #doneDot, #priorityGreenTag, #nodeGreen {{ color: palette(link); }}
        #priorityRedTag, #priorityYellowTag, #priorityGreenTag, #historyCount, #statusBadge, #journalJcr,
        #frontierScore, #frontierContentScore, #frontierJournalScore, #frontierAi, #frontierMust, #frontierWatch,
        #frontierExpand, #achievementType, #inspirationKind {{
            background: palette(button); border-radius: 7px; padding: 3px 6px; font-size: 10px;
        }}
        #historyCount, #statusBadge, #journalJcr, #frontierScore, #frontierAi, #frontierWatch {{ color: palette(highlight); }}
        #frontierContentScore {{ color: palette(text); }}
        #frontierJournalScore {{ color: palette(bright-text); }}
        #frontierLoadMoreButton, #manageJournalScoresButton {{ padding: 6px 10px; }}
        #journalScoreTable, #journalPriorityTable {{ background: palette(base); alternate-background-color: palette(alternate-base); border: 1px solid palette(shadow); }}
        #journalScoreTable QHeaderView::section, #journalPriorityTable QHeaderView::section {{ background: palette(alternate-base); color: palette(text); border: 0; border-right: 1px solid palette(shadow); border-bottom: 1px solid palette(shadow); padding: 5px 7px; font-weight: 700; }}
        #journalHealthChip, #journalCasBadge {{ background: palette(button); border-radius: 7px; padding: 3px 6px; font-size: 10px; }}
        #journalHealthChip {{ color: palette(mid); }}
        #journalHealthChip[verification="verified"] {{ color: palette(link); }}
        #journalHealthChip[verification="pending"] {{ color: palette(link-visited); }}
        #journalHealthChip[verification="warning"] {{ color: palette(bright-text); }}
        #journalCasBadge {{ color: palette(bright-text); }}
        #journalPublisherCheck {{ background: palette(button); border-radius: 7px; padding: 3px 2px; font-size: 10px; color: palette(mid); }}
        #journalPublisherCheck[validation="match"] {{ color: palette(link); }}
        #journalPublisherCheck[validation="probable"] {{ color: palette(highlight); }}
        #journalPublisherCheck[validation="mismatch"] {{ color: palette(link-visited); }}
        #inspirationBullet {{ color: palette(highlight); font-size: 14px; }}
        #inspirationKind, #readingUnread, #readingDone {{ color: palette(mid); font-size: 10px; }}
        #readingReason {{ color: palette(mid); font-size: 11px; }}
        #frontierExpand {{ color: palette(mid); }}
        #cardMeta, #cardLink, #journalUsage, #journalPublisher, #fileAttachmentIcon {{ color: palette(highlight); }}
        #cardLink:hover {{ color: palette(link); }}
        #rejectionArchiveFrame, #rejectionArchiveRow {{ background: palette(button); border: 1px solid palette(link-visited); border-radius: 9px; }}
        #archiveHeading, #archiveCount, #rejectionArchiveMarker {{ color: palette(link-visited); }}
        #paperLifecycleHint {{ background: palette(button); color: palette(link); border-radius: 7px; padding: 5px 7px; }}
        #futureDateIssueFrame {{ background: palette(button); border: 1px solid palette(bright-text); border-radius: 8px; }}
        #futureDateIssueLabel {{ color: palette(bright-text); }}
        #unlockOverlay {{ background: palette(button); border: 1px solid palette(highlight); border-radius: 4px; }}
        #unlockBrand {{ color: palette(window-text); font-size: 13px; font-weight: 700; }}
        #frontierReason, #frontierBrief, #frontierSummary, #inspirationText, #nodeText {{ color: palette(text); }}
        #profileFreshness, #profileSectionHint, #frontierSettingsHint, #profileEvidence,
        #profileRegionHint, #profileSource, #profileFieldLabel {{ color: palette(mid); }}
        #profileSectionHeading {{ color: palette(window-text); font-weight: 700; font-size: {card_title_size}px; }}
        #profileTermName {{ color: palette(window-text); font-weight: 700; }}
        #profileLocked {{ color: palette(link); background: palette(button); border-radius: 6px; padding: 2px 5px; font-size: 10px; }}
        #profileActiveState {{ color: palette(highlight); background: palette(button); border-radius: 6px; padding: 2px 5px; font-size: 10px; }}
        #researchSettingsTabs::pane {{ border: 0; border-top: 1px solid palette(shadow); top: -1px; }}
        #researchSettingsTabs QTabBar::tab {{
            min-width: 142px; min-height: 38px; padding: 0 18px;
            background: palette(button); color: palette(mid);
            border: 1px solid palette(shadow); border-bottom-color: palette(highlight);
        }}
        #researchSettingsTabs QTabBar::tab:first {{ border-top-left-radius: 5px; }}
        #researchSettingsTabs QTabBar::tab:last {{ border-top-right-radius: 5px; }}
        #researchSettingsTabs QTabBar::tab:selected {{
            background: palette(base); color: palette(text); font-weight: 700;
            border-color: palette(highlight); border-bottom: 3px solid palette(highlight);
        }}
        #profileRegionSplitter::handle:horizontal {{ width: 6px; background: transparent; margin: 0 1px; }}
        #activeTermsRegion, #pendingTermsRegion, #excludedTermsRegion {{
            background: palette(base); border: 1px solid palette(shadow); border-radius: 5px;
        }}
        #profileRegionHeader {{ color: palette(window-text); font-weight: 700; font-size: {card_title_size}px; }}
        #profileRegionCount {{ color: palette(highlight); background: palette(button); border-radius: 6px; min-width: 20px; padding: 2px 5px; font-size: 10px; }}
        #profileRegionHint {{ font-size: 10px; }}
        #profileActiveRow, #profilePendingRow, #profileExcludedRow {{
            background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 5px;
        }}
        #profileActiveRow[locked="true"], #profileExcludedRow[locked="true"] {{ border-color: palette(highlight); }}
        #profileRegionEmpty {{ color: palette(mid); background: palette(button); border-radius: 5px; padding: 10px; }}
        #profileWeightEdit {{ max-width: 68px; }}
        #profileStrategySection {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 6px; }}
        #profileWeightBar {{ border: 0; background: palette(button); border-radius: 3px; height: 6px; }}
        #profileWeightBar::chunk {{ background: palette(link); border-radius: 3px; }}
        #metricValue {{ color: palette(window-text); font-size: {card_title_size + 1}px; font-weight: 700; }}
        #todayTaskPreview {{ color: palette(text); background: palette(button); border-radius: 6px; padding: 5px 7px; }}
        #selectionAiStatus {{ background: palette(button); color: palette(mid); border: 1px solid palette(shadow); border-radius: 4px; padding: 4px 6px; }}
        #aiProgressPanel, #selectionProgressPanel, #selectionProgress, #frontierAiProgress, #journalLibraryAiProgress,
        #journalPickerAiProgress, #paperRecordAiProgress, #captureAiProgress, #researchKeywordProgress {{ background: palette(button); border: 1px solid palette(shadow); border-radius: 5px; }}
        #aiProgressStatus {{ color: palette(mid); font-size: 10px; }}
        #aiProgressBar, #selectionProgressBar {{ min-height: 10px; max-height: 10px; border: 1px solid palette(shadow); border-radius: 4px; background: palette(window); text-align: center; color: palette(text); }}
        #aiProgressBar::chunk, #selectionProgressBar::chunk {{ background: palette(highlight); border-radius: 3px; }}
        #selectionFilterSurface {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 8px; }}
        #selectionFilterLabel {{ color: palette(mid); font-size: 10px; }}
        #selectionMultiSelect, #selectionJcrMulti, #selectionCasMulti {{ text-align: left; padding-left: {spacing + 2}px; }}
        #selectionWorkbenchSplitter::handle:vertical {{ height: 5px; background: palette(shadow); margin: 2px 0; }}
        #selectionSummaryScroll, #selectionCandidateList, #selectionResults {{ background: palette(base); border: 1px solid palette(shadow); border-radius: 4px; }}
        #selectionSummaryPreview {{ background: transparent; color: palette(text); padding: 4px 6px; }}
        #selectionCandidateList::item, #selectionResults::item {{ border-bottom: 1px solid palette(shadow); padding: 0; }}
        #selectionCandidateList::item:selected, #selectionResults::item:selected {{ background: palette(light); }}
        #selectionRecommendationRow {{ background: transparent; }}
        #selectionRowTitle {{ color: palette(text); font-weight: 700; }}
        #selectionRowScore {{ color: palette(highlight); font-weight: 700; }}
        #selectionRowReason {{ color: palette(text); }}
        #selectionRowSummaryFacts {{ color: palette(mid); font-size: 10px; }}
        #selectionRowFacts {{ color: palette(mid); font-size: 10px; }}
        #selectionRowRisk {{ color: palette(bright-text); font-size: 10px; }}
        #selectionEvidenceBody {{ color: palette(mid); background: palette(button); border-left: 2px solid palette(highlight); padding: 6px 8px; font-size: 10px; }}
        #selectionEvidenceToggle {{ min-height: 26px; padding: 3px 8px; }}
        #selectionExcludeButton {{ color: palette(link-visited); border-color: palette(link-visited); }}
        #selectionStartButton, #selectionPathButton {{ background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); font-weight: 700; }}
        #frontierPreprintBadge {{ color: palette(mid); background: palette(button); border-radius: 4px; padding: 2px 5px; }}
        #specialIssuePageTitle {{ font-size: {card_title_size + 1}px; font-weight: 700; color: palette(text); }}
        #specialIssuePageSubtitle, #specialIssueMetricLabel, #specialIssuePreviewMeta, #specialIssueRefreshStatus {{ color: palette(mid); font-size: 10px; }}
        #specialIssueSummary {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 6px; }}
        #specialIssueUnreadCount, #specialIssueSavedCount, #specialIssueNearestDeadline {{ color: palette(highlight); font-weight: 700; font-size: 14px; }}
        #specialIssueSectionTitle {{ color: palette(mid); font-weight: 700; padding: 2px 1px; }}
        #specialIssuePreviewRow {{ background: palette(base); border-bottom: 1px solid palette(shadow); }}
        #specialIssuePreviewRow:hover {{ background: palette(alternate-base); border-left: 2px solid palette(highlight); }}
        #specialIssuePreviewTitle {{ color: palette(text); font-weight: 700; }}
        #specialIssuePreviewScore {{ color: palette(highlight); font-weight: 700; }}
        #specialIssuePreviewScroll, #specialIssuePreviewHost, #specialIssueSavedList, #specialIssueRecommendedList {{ background: transparent; border: 0; }}
        #specialIssueEmpty {{ color: palette(mid); }}
        #specialIssueOpenWorkbench {{ background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); font-weight: 700; }}
        #specialWorkbenchTitle {{ color: palette(text); font-size: {title_size - 3}px; font-weight: 700; }}
        #specialWorkbenchSplitter::handle:horizontal {{ width: 5px; background: palette(shadow); margin: 0 2px; }}
        #specialResultPane, #specialDetailPane, #specialDetailScroll {{ background: palette(window); border: 0; }}
        #specialResultList {{ background: palette(base); border: 1px solid palette(shadow); border-radius: 5px; }}
        #specialResultList::item {{ padding: 0; border-bottom: 1px solid palette(shadow); }}
        #specialResultList::item:selected {{ background: palette(light); }}
        #specialIssueResultRow {{ background: transparent; }}
        #specialIssueResultTitle, #specialDetailTitle {{ color: palette(text); font-weight: 700; }}
        #specialIssueResultScore {{ color: palette(highlight); font-size: 15px; font-weight: 700; }}
        #specialIssueResultState {{ color: palette(highlight); background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 3px; padding: 1px 5px; font-size: 10px; font-weight: 700; }}
        #specialIssueResultJournal {{ color: palette(text); font-size: 10px; font-weight: 600; }}
        #specialIssueResultMeta {{ color: palette(mid); font-size: 10px; }}
        #specialDetailFacts, #specialMatchedPapers, #specialActionStatus {{ color: palette(mid); }}
        #specialViewTabs::tab {{ min-width: 48px; padding: 7px 3px; color: palette(mid); }}
        #specialViewTabs::tab:selected {{ color: palette(text); font-weight: 700; border-bottom: 2px solid palette(highlight); }}
        #specialDetailTitle {{ font-size: {card_title_size + 2}px; }}
        #specialDetailReason {{ color: palette(text); background: palette(alternate-base); border-left: 3px solid palette(highlight); padding: 7px; }}
        #specialDetailSectionTitle {{ color: palette(text); font-weight: 700; }}
        #specialScopeText, #specialPersonalScopeNote, #specialEvidenceBrowser {{ background: palette(base); border: 1px solid palette(shadow); border-radius: 5px; padding: 5px; }}
        #specialIssueActions, #specialPaperActions {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 5px; }}
        #specialActionGroupLabel {{ color: palette(mid); font-size: 10px; font-weight: 700; }}
        #specialVerificationEvidence {{ background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 5px; }}
        #specialRefreshProgress {{ min-height: 12px; max-height: 12px; border: 1px solid palette(shadow); border-radius: 4px; background: palette(window); text-align: center; }}
        #specialRefreshProgress::chunk {{ background: palette(highlight); border-radius: 3px; }}
        #specialPathButton {{ background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); font-weight: 700; }}

        QScrollArea, QScrollArea::viewport, QAbstractScrollArea, QAbstractScrollArea::viewport {{ background: transparent; border: 0; }}
        QScrollBar:vertical {{ width: 7px; background: transparent; margin: 3px; }}
        QScrollBar::handle:vertical {{ background: palette(shadow); border-radius: 3px; min-height: 24px; }}
        QScrollBar::handle:vertical:hover {{ background: palette(highlight); }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{ height: 7px; background: transparent; margin: 3px; }}
        QScrollBar::handle:horizontal {{ background: palette(shadow); border-radius: 3px; min-width: 24px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    """


def build_dialog_stylesheet(theme_id: str | None = None, density: str | None = None) -> str:
    """Dialog-local sheet used by standalone editors created after the main window."""
    application = QApplication.instance()
    current_theme = theme_id or (application.property("research_assistant_theme_id") if application else None)
    current_density = density or (application.property("research_assistant_density") if application else None)
    return build_application_stylesheet(str(current_theme or "fog_teal"), str(current_density or "comfortable"))


def build_application_palette(theme_id: str | None = "fog_teal") -> QPalette:
    """Map semantic theme tokens to Qt palette roles without re-polishing widgets."""
    theme = get_theme(theme_id)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: theme["window"],
        QPalette.ColorRole.WindowText: theme["text"],
        QPalette.ColorRole.Base: theme["field"],
        QPalette.ColorRole.AlternateBase: theme["surface"],
        QPalette.ColorRole.ToolTipBase: theme["surface"],
        QPalette.ColorRole.ToolTipText: theme["text"],
        QPalette.ColorRole.Text: theme["text"],
        QPalette.ColorRole.Button: theme["raised"],
        QPalette.ColorRole.ButtonText: theme["text"],
        QPalette.ColorRole.BrightText: theme["warning"],
        QPalette.ColorRole.Light: theme["hover"],
        QPalette.ColorRole.Midlight: theme["selection"],
        QPalette.ColorRole.Mid: theme["muted"],
        QPalette.ColorRole.Dark: theme["border"],
        QPalette.ColorRole.Shadow: theme["border"],
        QPalette.ColorRole.Highlight: theme["accent"],
        QPalette.ColorRole.HighlightedText: theme["on_accent"],
        QPalette.ColorRole.Link: theme["success"],
        QPalette.ColorRole.LinkVisited: theme["danger"],
        QPalette.ColorRole.PlaceholderText: theme["muted"],
    }
    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        roles[accent_role] = theme["accent"]
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in roles.items():
            palette.setColor(group, role, QColor(color))
    for role, color in roles.items():
        muted = theme["muted"] if role in {
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.PlaceholderText,
        } else color
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(muted))
    return palette


class _SemanticThemeFilter(QObject):
    """Remove the legacy per-widget colour sheets as widgets become visible.

    Old releases styled each dialog and card separately.  Qt gives a widget's
    own stylesheet precedence over the application sheet, which is why merely
    setting a new global palette would leave a navy editor or grey popup
    behind.  Geometry is managed in layouts, so clearing a raw-colour local
    sheet is safe and lets the object-name rules above take over.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API spelling
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            style = watched.styleSheet()
            if "#" in style and not bool(watched.property("_research_assistant_legacy_style_clear_pending")):
                # Changing QSS inside a Show event recursively shows legacy
                # child widgets.  Clear it after the current show stack ends.
                watched.setProperty("_research_assistant_legacy_style_clear_pending", True)
                QTimer.singleShot(0, lambda widget=watched: self._clear_legacy_style(widget))
        return False

    @staticmethod
    def _clear_legacy_style(widget: QWidget) -> None:
        try:
            if "#" in widget.styleSheet():
                widget.setStyleSheet("")
        except RuntimeError:
            # A queued clear may outlive a dialog that was already dismissed.
            return
        finally:
            try:
                widget.setProperty("_research_assistant_legacy_style_clear_pending", False)
            except RuntimeError:
                pass


def _install_semantic_theme_filter(application: QApplication) -> None:
    existing = getattr(application, "_research_assistant_theme_filter", None)
    if isinstance(existing, _SemanticThemeFilter):
        return
    event_filter = _SemanticThemeFilter(application)
    application.installEventFilter(event_filter)
    application._research_assistant_theme_filter = event_filter  # type: ignore[attr-defined]


def apply_application_theme(application: QApplication, theme_id: str | None, density: str | None = "comfortable") -> bool:
    """Apply a palette theme and return whether any visual work was necessary.

    Setting an application stylesheet causes Qt to re-polish all existing
    children.  The previous implementation did that for every Settings save,
    including an unchanged save.  Refresh it only for a genuine theme or
    density change, so Qt re-evaluates palette-backed QSS while routine saves
    stay inexpensive.
    """
    normalized_theme = _theme_id(theme_id)
    normalized_density = normalize_density(density)
    previous_theme = str(application.property("research_assistant_theme_id") or "")
    previous_density = str(application.property("research_assistant_density") or "")
    engine_ready = application.property("research_assistant_theme_engine") == "palette-v3"
    theme_changed = previous_theme != normalized_theme
    density_changed = previous_density != normalized_density
    if engine_ready and not theme_changed and not density_changed:
        return False

    application.setProperty("research_assistant_theme_id", normalized_theme)
    application.setProperty("research_assistant_density", normalized_density)
    _install_semantic_theme_filter(application)
    palette = build_application_palette(normalized_theme)
    application.setPalette(palette)
    # Qt's stylesheet engine gives top-level widgets a local palette when the
    # application sheet is installed.  Those copies do not follow a later
    # QApplication palette change, which made an already-open Settings dialog
    # look unchanged after selecting a new theme.  Refresh the small set of
    # top-level windows explicitly; child widgets inherit from their window.
    for widget in application.topLevelWidgets():
        if isinstance(widget, QWidget):
            widget.setPalette(palette)
    if not engine_ready or density_changed or theme_changed:
        stylesheet = build_application_stylesheet(normalized_theme, normalized_density)
        if engine_ready:
            application.setStyleSheet("")
        application.setStyleSheet(stylesheet)
    application.setProperty("research_assistant_theme_engine", "palette-v3")
    return True


def apply_dialog_theme(dialog: QDialog, *, keep_custom: bool = False) -> None:
    """Let a dialog inherit the current semantic application palette.

    Dialog-local copies of the full QSS were a second expensive re-polish pass
    during Settings opening.  Geometry is controlled by layouts, so clearing a
    legacy local colour sheet is sufficient and preserves any requested custom
    style for the rare caller that still owns one.
    """
    application = QApplication.instance()
    if application is not None:
        dialog.setPalette(application.palette())
    if not keep_custom:
        dialog.setStyleSheet("")


# Backward-compatible name for older imports. It is generated from the default
# theme rather than freezing the former navy palette.
DIALOG_BASE_STYLE = build_application_stylesheet("fog_teal", "comfortable")
