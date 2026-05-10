"""
Admin widget – user management panel (admin-only).
Full-width table with Add / Edit / Delete toolbar above.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt

from trade_relay.i18n import t
from trade_relay.auth.manager import (
    Session, create_user, delete_user, reset_user_password, update_user,
)
from trade_relay import database as db

_COL_USERNAME  = 0
_COL_ROLE      = 1
_COL_ACTIVE    = 2
_COL_API_KEY   = 3
_COL_API_SECRET = 4
_COL_CREATED   = 5
_COL_UPDATED   = 6


# ─────────────────────────────────────────────────────────────
# Dialog: Add / Edit user
# ─────────────────────────────────────────────────────────────
class UserFormDialog(QDialog):
    """Modal form for creating or editing a user."""

    def __init__(self, session: Session, user_id: int | None = None, parent=None):
        super().__init__(parent)
        self._session = session
        self._user_id = user_id
        self._is_edit = user_id is not None
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setWindowTitle(t("edit_user") if self._is_edit else t("create_user"))
        self._setup_ui()
        if self._is_edit:
            self._load_user()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText(t("username"))
        if self._is_edit:
            self._username_edit.setReadOnly(True)
            self._username_edit.setEnabled(False)
        form.addRow(t("username"), self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if self._is_edit:
            self._password_edit.setPlaceholderText(t("new_password_optional"))
            form.addRow(t("new_password"), self._password_edit)
        else:
            self._password_edit.setPlaceholderText(t("password"))
            form.addRow(t("password"), self._password_edit)

            self._confirm_edit = QLineEdit()
            self._confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._confirm_edit.setPlaceholderText(t("confirm_password"))
            form.addRow(t("confirm_password"), self._confirm_edit)

        self._role_combo = QComboBox()
        self._role_combo.addItem(t("role_user"), "user")
        self._role_combo.addItem(t("role_admin"), "admin")
        form.addRow(t("role"), self._role_combo)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText(t("api_key"))
        form.addRow(t("api_key"), self._api_key_edit)

        self._api_secret_edit = QLineEdit()
        self._api_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_secret_edit.setPlaceholderText(t("api_secret"))
        form.addRow(t("api_secret"), self._api_secret_edit)

        layout.addLayout(form)

        self._notice = QLabel("")
        self._notice.setMinimumHeight(18)
        layout.addWidget(self._notice)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load_user(self) -> None:
        row = db.get_user_by_id(self._user_id)
        if row is None:
            return
        self._username_edit.setText(row["username"])
        idx = self._role_combo.findData(row["role"])
        if idx >= 0:
            self._role_combo.setCurrentIndex(idx)
        self._api_key_edit.setText(
            db.decrypt_api_credential(row.get("binance_api_key") or "")
        )
        self._api_secret_edit.setText(
            db.decrypt_api_credential(row.get("binance_api_secret") or "")
        )

    def _submit(self) -> None:
        role = self._role_combo.currentData()
        api_key = self._api_key_edit.text().strip()
        api_secret = self._api_secret_edit.text().strip()

        if self._is_edit:
            new_pwd = self._password_edit.text()
            if new_pwd:
                ok, msg = reset_user_password(self._session, self._user_id, new_pwd)
                if not ok:
                    self._set_notice(msg, "error")
                    return
            ok, msg = update_user(self._session, self._user_id, role, api_key, api_secret)
            if not ok:
                self._set_notice(msg, "error")
                return
            self.accept()
        else:
            username = self._username_edit.text().strip()
            password = self._password_edit.text()
            confirm = self._confirm_edit.text()
            if password != confirm:
                self._set_notice(t("passwords_mismatch"), "error")
                return
            ok, msg = create_user(self._session, username, password, role, api_key, api_secret)
            if not ok:
                self._set_notice(msg, "error")
                return
            self.accept()

    def _set_notice(self, msg: str, level: str = "muted") -> None:
        colors = {"success": "#56d364", "error": "#f85149", "muted": "#8b949e"}
        self._notice.setStyleSheet(f"color: {colors.get(level, '#e6edf3')};")
        self._notice.setText(msg)


# ─────────────────────────────────────────────────────────────
# Main admin widget
# ─────────────────────────────────────────────────────────────
class AdminWidget(QWidget):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Toolbar ───────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # push buttons to the right
        toolbar.addStretch()

        self._notice = QLabel("")
        self._notice.setMinimumHeight(18)
        toolbar.addWidget(self._notice)

        self._add_btn = QPushButton(t("add_user"))
        self._add_btn.setObjectName("primary_sm")
        self._add_btn.clicked.connect(self._add_user)
        toolbar.addWidget(self._add_btn)

        self._edit_btn = QPushButton(t("edit_user"))
        self._edit_btn.setObjectName("primary_sm")
        self._edit_btn.clicked.connect(self._edit_user)
        toolbar.addWidget(self._edit_btn)

        self._delete_btn = QPushButton(t("delete_user"))
        self._delete_btn.setObjectName("danger_sm")
        self._delete_btn.clicked.connect(self._delete_users)
        toolbar.addWidget(self._delete_btn)

        self._activate_btn = QPushButton(t("activate_user"))
        self._activate_btn.setObjectName("primary_sm")
        self._activate_btn.clicked.connect(self._activate_users)
        toolbar.addWidget(self._activate_btn)

        self._freeze_btn = QPushButton(t("freeze_user"))
        self._freeze_btn.setObjectName("danger_sm")
        self._freeze_btn.clicked.connect(self._freeze_users)
        toolbar.addWidget(self._freeze_btn)

        layout.addLayout(toolbar)

        # ── Table ─────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            t("col_username"), t("col_role"), t("col_active"),
            t("col_api_key"), t("col_api_secret"),
            t("col_created"), t("col_updated"),
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_USERNAME,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_ROLE,       QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_ACTIVE,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_API_KEY,    QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_API_SECRET, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_CREATED,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_UPDATED,    QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        self._load_users()

    def _load_users(self) -> None:
        self._table.clearSelection()
        users = db.get_all_users()
        self._table.setRowCount(len(users))
        for row, u in enumerate(users):
            api_key    = db.decrypt_api_credential(u.get("binance_api_key") or "")
            api_secret = db.decrypt_api_credential(u.get("binance_api_secret") or "")
            is_active  = bool(u["is_active"])
            vals = [
                u["username"],
                t("role_admin") if u["role"] == "admin" else t("role_user"),
                t("yes") if is_active else t("no"),
                api_key,
                api_secret,
                str(u["created_at"])[:16] if u.get("created_at") else "",
                str(u["updated_at"])[:16] if u.get("updated_at") else "",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole,     u["id"])
                item.setData(Qt.ItemDataRole.UserRole + 1, u["role"])
                item.setData(Qt.ItemDataRole.UserRole + 2, is_active)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

    def _selected_ids(self) -> list[int]:
        seen: set[int] = set()
        ids: list[int] = []
        for item in self._table.selectedItems():
            uid = item.data(Qt.ItemDataRole.UserRole)
            if uid not in seen:
                seen.add(uid)
                ids.append(uid)
        return ids

    def _get_row_role(self, user_id: int) -> str:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == user_id:
                return item.data(Qt.ItemDataRole.UserRole + 1) or ""
        return ""

    def _get_row_active(self, user_id: int) -> bool:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == user_id:
                return bool(item.data(Qt.ItemDataRole.UserRole + 2))
        return False

    def _on_selection_changed(self) -> None:
        pass  # all buttons always enabled

    def _add_user(self) -> None:
        dlg = UserFormDialog(self._session, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_notice(t("user_created", ""), "success")
            self._load_users()

    def _edit_user(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            self._set_notice("Please select exactly one user to edit.", "error")
            return
        dlg = UserFormDialog(self._session, user_id=ids[0], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_notice(t("user_updated", ""), "success")
            self._load_users()

    def _delete_users(self) -> None:
        ids = self._selected_ids()
        deletable = [
            uid for uid in ids
            if uid != self._session.user_id
            and self._get_row_role(uid) != "admin"
        ]
        if not deletable:
            self._set_notice("Select one or more non-admin users to delete.", "error")
            return

        reply = QMessageBox.question(
            self,
            t("confirm"),
            t("confirm_delete_users", len(deletable)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        failed = 0
        for uid in deletable:
            ok, _ = delete_user(self._session, uid)
            if not ok:
                failed += 1

        if failed:
            self._set_notice(t("delete_partial", failed), "error")
        else:
            self._set_notice(t("delete_success", len(deletable)), "success")
        self._load_users()

    def _activate_users(self) -> None:
        ids = self._selected_ids()
        targets = [
            uid for uid in ids
            if uid != self._session.user_id
            and self._get_row_role(uid) != "admin"
            and not self._get_row_active(uid)
        ]
        if not targets:
            self._set_notice("Select one or more inactive non-admin users to activate.", "error")
            return
        for uid in targets:
            db.activate_user(uid)
        self._set_notice(t("user_activated", ""), "success")
        self._load_users()

    def _freeze_users(self) -> None:
        ids = self._selected_ids()
        targets = [
            uid for uid in ids
            if uid != self._session.user_id
            and self._get_row_role(uid) != "admin"
            and self._get_row_active(uid)
        ]
        if not targets:
            self._set_notice("Select one or more active non-admin users to deactivate.", "error")
            return
        for uid in targets:
            db.deactivate_user(uid)
        self._set_notice(t("user_frozen", ""), "success")
        self._load_users()

    def _set_notice(self, msg: str, level: str = "muted") -> None:
        colors = {"success": "#56d364", "error": "#f85149", "muted": "#8b949e"}
        self._notice.setStyleSheet(f"color: {colors.get(level, '#e6edf3')};")
        self._notice.setText(msg)

