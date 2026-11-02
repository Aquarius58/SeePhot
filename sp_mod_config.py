"""Versioned user configuration, explicit BAV import and shared Config editor."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import uuid

import sp_mod_results as results
from pathlib import Path
import sys


PLUGIN_TAB_LABEL = "Config"
CONFIG_NAME = "config.json"
DEFAULT_VSX_LIMIT_MAG = 16.0
VSX_LIMIT_MAG_MIN = 5.0
VSX_LIMIT_MAG_MAX = 20.0
_session_site_id: str | None = None


def user_config_dir() -> Path:
    """Return the OS-specific SeePhot user configuration directory."""

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SeePhot"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "SeePhot"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg_config_home) if xdg_config_home else Path.home() / ".config") / "SeePhot"


def config_path() -> Path:
    """Return the global configuration file path."""

    return user_config_dir() / CONFIG_NAME


SCHEMA_VERSION = 3


class ConfigError(ValueError):
    """Configuration cannot be safely loaded or written."""


def defaults() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "vsx": {"limit_magnitude": DEFAULT_VSX_LIMIT_MAG},
        "observer": {"name": "", "aavso_code": ""},
        "sites": [{"id": "primary", "name": "", "latitude_deg": None, "longitude_deg": None}],
        "default_site_id": "primary",
        "bav": {"observer_code": ""},
    }


def validate(data: dict) -> dict:
    """Validate complete site coordinates; allow only the empty initial placeholder."""
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object.")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"Unsupported configuration version: {data.get('schema_version')!r}.")
    clean = defaults()
    unknown = set(data) - set(clean)
    if unknown:
        raise ConfigError(f"Unknown configuration sections: {', '.join(sorted(unknown))}.")
    for section, fields in clean.items():
        if section in {"schema_version", "sites", "default_site_id", "bav"}:
            continue
        incoming = data.get(section, {})
        if not isinstance(incoming, dict):
            raise ConfigError(f"{section} must be an object.")
        if set(incoming) - set(fields):
            raise ConfigError(f"Unknown fields in {section}.")
        fields.update(incoming)
    # BAV owns this optional section. Preserve it even when unusable by BAV;
    # shared observer/site settings must remain readable and editable.
    clean["bav"] = copy.deepcopy(data.get("bav", clean["bav"]))
    for section, key in (("observer", "name"), ("observer", "aavso_code")):
        value = clean[section][key]
        if not isinstance(value, str):
            raise ConfigError(f"{section}.{key} must be text.")
        clean[section][key] = value.strip()
    for section, key, pattern in (("observer", "aavso_code", r"[A-Z0-9]{1,5}"),):
        value = clean[section][key].upper()
        if value and not re.fullmatch(pattern, value):
            raise ConfigError(f"Invalid {section}.{key}.")
        clean[section][key] = value
    value = clean["vsx"]["limit_magnitude"]
    if type(value) not in (int, float) or not math.isfinite(value) or not VSX_LIMIT_MAG_MIN <= value <= VSX_LIMIT_MAG_MAX:
        raise ConfigError("vsx.limit_magnitude must be a finite number in 5..20 mag.")
    clean["vsx"]["limit_magnitude"] = float(value)
    sites = data.get("sites", clean["sites"])
    if not isinstance(sites, list) or not sites:
        raise ConfigError("sites must be a non-empty list.")
    normalized = []
    ids, names = set(), set()
    for site in sites:
        if not isinstance(site, dict) or set(site) != {"id", "name", "latitude_deg", "longitude_deg"}:
            raise ConfigError("Each site requires id, name, latitude_deg and longitude_deg.")
        site = dict(site)
        for key in ("id", "name"):
            if not isinstance(site[key], str):
                raise ConfigError(f"Site {key} must be text.")
            site[key] = site[key].strip()
        if not site["id"] or site["id"] in ids:
            raise ConfigError("Site IDs must be non-empty and unique.")
        # A single incomplete initial profile remains permitted.
        name = site["name"].casefold()
        if (not name and len(sites) > 1) or name in names:
            raise ConfigError("Multiple sites require non-empty, unique names.")
        ids.add(site["id"])
        names.add(name)
        for key, limit in (("latitude_deg", 90), ("longitude_deg", 180)):
            value = site[key]
            if value is not None:
                if type(value) not in (int, float) or abs(value) > limit or not math.isfinite(value):
                    raise ConfigError(f"Site {key} must be a finite number in {-limit}..{limit} or null.")
                site[key] = float(value)
        placeholder = (len(sites) == 1 and not site["name"]
                       and site["latitude_deg"] is None and site["longitude_deg"] is None)
        if not placeholder and (not site["name"] or site["latitude_deg"] is None or site["longitude_deg"] is None):
            raise ConfigError("Site requires a name and complete latitude and longitude.")
        normalized.append(site)
    clean["sites"] = normalized
    default_id = data.get("default_site_id", clean["default_site_id"])
    if not isinstance(default_id, str) or default_id not in ids:
        raise ConfigError("default_site_id must identify an existing site.")
    clean["default_site_id"] = default_id
    return clean


def validate_bav_section(section: object) -> dict[str, str]:
    """Validate only the current BAV section, at BAV use/edit boundaries."""
    if not isinstance(section, dict) or set(section) - {"observer_code"}:
        raise ConfigError("BAV configuration requires an object with observer_code only.")
    value = section.get("observer_code", "")
    if not isinstance(value, str):
        raise ConfigError("bav.observer_code must be text.")
    value = value.strip().upper()
    if value and not re.fullmatch(r"[A-Z]+", value):
        raise ConfigError("Invalid bav.observer_code.")
    return {"observer_code": value}


def default_site(data: dict) -> dict:
    """Resolve the saved standard site from a validated configuration."""
    return next(site for site in data["sites"] if site["id"] == data["default_site_id"])


def convert_v1_to_v2(data: dict) -> dict:
    """Retire the user telescope default; retain shared personal data."""
    if set(data) - {"schema_version", "observer", "site", "bav", "instruments"}:
        raise ConfigError("Unknown fields in version 1 configuration.")
    instruments = data.get("instruments", {})
    if not isinstance(instruments, dict) or set(instruments) - {"default_telescope"}:
        raise ConfigError("Unsupported instrument fields in version 1 configuration.")
    data.pop("instruments", None)
    data["schema_version"] = 2
    return data


def convert_v2_to_v3(data: dict) -> dict:
    """Preserve the former single site as the standard site."""
    if set(data) - {"schema_version", "observer", "site", "bav"}:
        raise ConfigError("Unknown fields in version 2 configuration.")
    site = data.pop("site", {"name": "", "latitude_deg": None, "longitude_deg": None})
    if not isinstance(site, dict) or set(site) - {"name", "latitude_deg", "longitude_deg"}:
        raise ConfigError("Unsupported site fields in version 2 configuration.")
    data["sites"] = [{"id": "primary", "name": "", "latitude_deg": None,
                       "longitude_deg": None, **site}]
    data["default_site_id"] = "primary"
    data["schema_version"] = 3
    return data


VERSION_CONVERTERS = {1: convert_v1_to_v2, 2: convert_v2_to_v3}


def read_document(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration must be an object: {path}")
    return data


def convert_versions(data: dict) -> dict:
    """Run only registered adjacent steps, then validate the current schema."""
    converted = copy.deepcopy(data)
    version = converted.get("schema_version")
    if type(version) is not int or not 1 <= version <= SCHEMA_VERSION:
        raise ConfigError(f"Unsupported configuration version: {version!r}.")
    while version < SCHEMA_VERSION:
        converter = VERSION_CONVERTERS.get(version)
        if converter is None:
            raise ConfigError(f"No configuration converter for version {version}.")
        try:
            converted = converter(copy.deepcopy(converted))
        except Exception as exc:
            raise ConfigError(f"Configuration conversion from version {version} failed: {exc}") from exc
        if not isinstance(converted, dict) or type(converted.get("schema_version")) is not int or converted["schema_version"] != version + 1:
            raise ConfigError(f"Converter {version} did not produce version {version + 1}.")
        version += 1
    return validate(converted)


def backup(path: Path) -> Path:
    """Preserve exact source bytes using an exclusive, never overwritten file."""
    payload = path.read_bytes()
    index = 0
    while True:
        target = path.with_name(path.name + (".bak" if index == 0 else f".bak.{index}"))
        try:
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            return target
        except FileExistsError:
            index += 1


def atomic_write(path: Path, data: dict) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_bav(data: dict) -> dict:
    """Convert the explicitly supported unversioned BAV document once."""
    target = defaults()
    mapping = {"observer_name": ("observer", "name"), "aavso_code": ("observer", "aavso_code"),
               "site_name": ("site", "name"), "bav_code": ("bav", "observer_code")}
    coordinate_keys = {f"{prefix}_{part}" for prefix in ("latitude", "longitude")
                       for part in ("hemisphere", "degrees", "minutes", "seconds")}
    if not data or set(data) - (set(mapping) | coordinate_keys | {"telescope"}):
        raise ConfigError("Unsupported BAV import document.")
    for old_key, (section, key) in mapping.items():
        if old_key in data:
            ((target["sites"][0] if section == "site" else target[section]))[key] = data[old_key]
    for prefix, positive, negative, limit in (("latitude", "N", "S", 90), ("longitude", "O", "W", 180)):
        keys = [f"{prefix}_{part}" for part in ("hemisphere", "degrees", "minutes", "seconds")]
        values = [data.get(key) for key in keys]
        if all(value is None or value == "" for value in values):
            continue
        if any(value is None or value == "" for value in values):
            raise ConfigError(f"Incomplete BAV {prefix} coordinate.")
        hemisphere = str(values[0]).strip().upper()
        try:
            degrees, minutes, seconds = [float(str(value).strip().replace(",", ".")) for value in values[1:]]
        except ValueError as exc:
            raise ConfigError(f"Invalid BAV {prefix} coordinate.") from exc
        if (hemisphere not in (positive, negative) or
            not all(math.isfinite(value) for value in (degrees, minutes, seconds)) or
            not degrees.is_integer() or not minutes.is_integer() or
            not 0 <= degrees <= limit or not 0 <= minutes <= 59 or not 0 <= seconds <= 59.9 or
            (degrees == limit and (minutes or seconds))):
            raise ConfigError(f"Invalid BAV {prefix} coordinate.")
        target["sites"][0][prefix + "_deg"] = (-1 if hemisphere == negative else 1) * (degrees + minutes / 60 + seconds / 3600)
    return validate(target)


def read_current_config(path: Path) -> tuple[dict, list[str]]:
    """Load the current schema, omitting invalid sites without changing the file."""
    data = read_document(path)
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"Unsupported configuration version: {data.get('schema_version')!r}.")
    sites = data.get("sites", defaults()["sites"])
    if not isinstance(sites, list) or not sites:
        raise ConfigError("sites must be a non-empty list.")
    valid_sites, messages = [], []
    for index, site in enumerate(sites, 1):
        try:
            probe = defaults()
            probe["sites"] = [site]
            probe["default_site_id"] = site.get("id") if isinstance(site, dict) else None
            checked = validate(probe)["sites"][0]
            if len(sites) > 1 and not checked["name"]:
                raise ConfigError("An empty start placeholder is not a site.")
            valid_sites.append(checked)
        except ConfigError as exc:
            messages.append(f"WARNING: Site {index} not loaded: {exc}")
    if not valid_sites:
        raise ConfigError("No valid sites in configuration. " + " ".join(messages))
    clean = validate({**data, "sites": valid_sites})
    try:
        validate_bav_section(clean["bav"])
    except ConfigError as exc:
        messages.append(f"WARNING: BAV configuration: {exc}")
    return clean, messages


def initialize(path: Path | None = None) -> tuple[dict, list[str]]:
    """Initialize or migrate once; failure never replaces existing user data."""
    path = path or config_path()
    try:
        if path.exists():
            original = read_document(path)
            if type(original.get("schema_version")) is int and original["schema_version"] == SCHEMA_VERSION:
                return read_current_config(path)
            clean = convert_versions(original)
            if original["schema_version"] != SCHEMA_VERSION:
                saved_backup = backup(path)
                atomic_write(path, clean)
                return clean, [f"Configuration migrated; backup: {saved_backup}"]
            return clean, []
        old_path = path.with_name("bav_report.json")
        if old_path.exists():
            try:
                clean = import_bav(read_document(old_path))
                saved_backup = backup(old_path)
            except (OSError, ValueError) as exc:
                clean = defaults()
                atomic_write(path, clean)
                return clean, [f"WARNING: BAV configuration not imported; source preserved: {old_path} ({exc})",
                               f"Configuration created: {path}"]
            atomic_write(path, clean)
            return clean, [f"BAV configuration imported; backup: {saved_backup}"]
        clean = defaults()
        atomic_write(path, clean)
        return clean, [f"Configuration created: {path}"]
    except (OSError, ValueError) as exc:
        return defaults(), [f"WARNING: {exc}"]


def load_config(path: Path | None = None) -> dict:
    """Read current configuration without side effects or historical fallbacks."""
    path = path or config_path()
    if not path.exists():
        return defaults()
    return read_current_config(path)[0]


def save_config(data: dict, path: Path | None = None, *, expected: dict | None = None) -> Path:
    path = path or config_path()
    clean = validate(data)
    if path.exists():
        current = load_config(path)  # Protect corrupt/unsupported documents.
        if expected is not None and current != expected:
            raise ConfigError("Configuration changed externally. Reload before saving.")
    atomic_write(path, clean)
    return path


def reset_app_defaults(path: Path | None = None, *, expected: dict | None = None) -> Path:
    """Reset only application preferences, preserving all personal sections."""
    data = load_config(path)
    data["vsx"] = defaults()["vsx"]
    return save_config(data, path, expected=expected)


def current_site(data: dict) -> dict:
    """Return the session selection, falling back to the saved default."""
    return next((site for site in data["sites"] if site["id"] == _session_site_id), default_site(data))


def observer_result_settings(data: dict | None = None) -> dict[str, str]:
    clean = validate(data) if data is not None else load_config()
    site = default_site(clean) if data is not None else current_site(clean)
    try:
        bav_code = validate_bav_section(clean["bav"])["observer_code"]
    except ConfigError:
        bav_code = ""
    return {
        "TELESCOPE": results.DEFAULT_RESULT_TELESCOPE,
        "TELESCOPE_SOURCE": "built-in default",
        "OBSERVER_BAV": bav_code,
        "OBSERVER_NAME": clean["observer"]["name"],
        "OBSERVER_AAVSO": clean["observer"]["aavso_code"],
        "OBSERVER_SITE": site["name"],
        "OBSERVER_LATITUDE": "" if site["latitude_deg"] is None else str(site["latitude_deg"]),
        "OBSERVER_LONGITUDE": "" if site["longitude_deg"] is None else str(site["longitude_deg"]),
    }


def bav_settings(data: dict | None = None) -> dict[str, str]:
    """Project shared values into the BAV export settings contract (no file IO)."""
    clean = validate(data) if data is not None else load_config()
    site = default_site(clean) if data is not None else current_site(clean)
    settings = {"observer_name": clean["observer"]["name"],
                "aavso_code": clean["observer"]["aavso_code"],
                "bav_code": validate_bav_section(clean["bav"])["observer_code"],
                "site_name": site["name"],
                "telescope": results.DEFAULT_RESULT_TELESCOPE}
    # BAV's existing export validation accepts DMS. Use full precision instead
    # of rounding through the former UI's one-decimal-second spin boxes.
    for prefix, positive, negative in (("latitude", "N", "S"), ("longitude", "O", "W")):
        value = site[prefix + "_deg"]
        if value is None:
            for part in ("hemisphere", "degrees", "minutes", "seconds"):
                settings[prefix + "_" + part] = ""
            continue
        total = abs(value) * 3600
        degrees = int(total // 3600)
        remainder = total - degrees * 3600
        minutes = int(remainder // 60)
        seconds = remainder - minutes * 60
        settings.update({prefix + "_hemisphere": negative if value < 0 else positive,
                         prefix + "_degrees": str(degrees), prefix + "_minutes": str(minutes),
                         prefix + "_seconds": str(seconds)})
    return settings


def create_config_tab(context: dict | None = None):
    """Build the shared observer/site editor; save only through this backend."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QGroupBox, QLabel, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

    from PyQt6.QtGui import QValidator

    class CheckedSpinBox(QSpinBox):
        def validate(self, text, pos):
            result = super().validate(text, pos)
            if result[0] == QValidator.State.Invalid:
                return QValidator.State.Intermediate, text, pos
            return result

    class CheckedDoubleSpinBox(QDoubleSpinBox):
        def validate(self, text, pos):
            result = super().validate(text, pos)
            if result[0] == QValidator.State.Invalid:
                return QValidator.State.Intermediate, text, pos
            return result

    context = context or {}
    tab = QWidget()
    tab.setProperty("context_help_body", (
        "<h3>Observer</h3><ul><li>Enter your <b>Observer name</b> and "
        "<b>AAVSO code</b>; enter your <b>BAV code</b> when BAV is available. "
        "These details apply to all sites. Click <b>Save</b> to store changes.</li></ul>"
        "<h3>VSX</h3><ul><li><b>Query limit</b> sets the faintest catalog maximum "
        "brightness included in VSX queries, shared by all sites. A larger magnitude "
        "includes fainter stars; it does not guarantee usable photometry. Click <b>Save</b> "
        "to apply the setting to the next query. To replace an already loaded list, "
        "run <b>Detect Variables</b> again in <b>Prepare</b>.</li></ul>"
        "<h3>Sites</h3><ul><li>Choose a saved <b>Site</b> for subsequent measurements. "
        "The selection applies immediately without <b>Save</b> and does not change "
        "the startup default.</li>"
        "<li>Use <b>Edit</b> to change a site's name and coordinates, or <b>Add</b> "
        "for a new site. Enter a unique site name and complete latitude and longitude "
        "in degrees, minutes and seconds, choosing <b>N/S</b> and <b>E/W</b> as appropriate. "
        "Click <b>Save</b> before using new or edited values.</li>"
        "<li><b>Set default</b>, then <b>Save</b>, sets the site selected at startup, "
        "marked <b>(default)</b>. <b>Delete</b> removes a non-default site; "
        "the last site cannot be deleted.</li></ul>"
        "<h3>Save and Reload</h3><ul><li><b>Save</b> stores all Config changes together. "
        "Check the log for save errors.</li>"
        "<li><b>Reload</b> discards all unsaved changes and selects the saved default site.</li>"
        "<li>Changing Config does not rewrite the site recorded in existing results.</li></ul>"
    ))
    layout = QVBoxLayout(tab)
    observer_group = QGroupBox("Observer — shared by all sites")
    observer_row = QHBoxLayout(observer_group)
    top_row = QHBoxLayout()
    top_row.addWidget(observer_group)
    vsx_group = QGroupBox("VSX")
    vsx_row = QHBoxLayout(vsx_group)
    vsx_row.addWidget(QLabel("Query limit"))
    vsx_limit = QDoubleSpinBox()
    vsx_limit.setObjectName("vsx.limit_magnitude")
    vsx_limit.setRange(VSX_LIMIT_MAG_MIN, VSX_LIMIT_MAG_MAX)
    vsx_limit.setFixedHeight(QLineEdit().sizeHint().height())
    vsx_limit.setDecimals(1)
    vsx_limit.setSingleStep(0.1)
    vsx_limit.setSuffix(" mag")
    vsx_limit.setToolTip("Catalog maximum brightness limit; Save applies it to the next VSX query.")
    vsx_row.addWidget(vsx_limit)
    top_row.addWidget(vsx_group)
    top_row.addStretch(1)
    layout.addLayout(top_row)
    site_group = QGroupBox("Sites — name and coordinates")
    site_layout = QVBoxLayout(site_group)
    layout.addWidget(site_group)
    form_row = QHBoxLayout()
    form = QFormLayout()
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    coordinates_form = QFormLayout()
    coordinates_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    coordinates_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    edits = {}
    for section, key, label in (("observer", "name", "Observer name"), ("observer", "aavso_code", "AAVSO code"),
                              ("bav", "observer_code", "BAV code"), ("site", "name", "Site name")):
        if section == "bav" and not context.get("bav_available", False):
            continue
        edit = QLineEdit()
        edit.setObjectName(section + "." + key)
        if section == "site":
            edit.setFixedWidth(280)
        elif key == "name":
            edit.setFixedWidth(180)
        else:
            edit.setFixedWidth(edit.fontMetrics().horizontalAdvance("MMMMMM") + 20)
        edits[section, key] = edit
        if section == "site":
            coordinates_form.addRow(label, edit)
        else:
            observer_row.addWidget(QLabel(label))
            observer_row.addWidget(edit)
    observer_row.addStretch(1)
    site_selector = QComboBox()
    site_selector.setObjectName("sites.selected")
    edit_site = QPushButton("Edit")
    default_button = QPushButton("Set default")
    site_actions = QHBoxLayout()
    add_site = QPushButton("Add")
    delete_site = QPushButton("Delete")
    site_actions.addWidget(edit_site)
    site_actions.addWidget(default_button)
    site_actions.addWidget(add_site)
    site_actions.addWidget(delete_site)
    form.addRow("Site", site_selector)
    form.addRow(site_actions)
    coordinates = {}
    for prefix, label, hemispheres, limit in (("latitude", "Latitude", ("N", "S"), 90),
                                             ("longitude", "Longitude", ("E", "W"), 180)):
        row = QHBoxLayout()
        hemisphere = QComboBox()
        hemisphere.addItems(hemispheres)
        hemisphere.setFixedWidth(56)
        degrees = CheckedSpinBox()
        degrees.setAlignment(Qt.AlignmentFlag.AlignRight)
        degrees.setRange(-1, 360)
        degrees.setSpecialValueText("—")
        degrees.setToolTip("Unknown coordinate: select — below 0 degrees.")
        degrees.setSuffix("°")
        degrees.setFixedWidth(70)
        minutes = CheckedSpinBox()
        minutes.setAlignment(Qt.AlignmentFlag.AlignRight)
        minutes.setRange(0, 59)
        minutes.setSuffix("'")
        minutes.setFixedWidth(64)
        seconds = CheckedDoubleSpinBox()
        seconds.setAlignment(Qt.AlignmentFlag.AlignRight)
        seconds.setDecimals(1)
        seconds.setRange(0, 59.9)
        seconds.setSingleStep(0.1)
        seconds.setSuffix('"')
        seconds.setFixedWidth(82)
        entry = { "hemisphere": hemisphere, "degrees": degrees, "minutes": minutes,
                 "seconds": seconds, "dirty": False, "limit": limit, "negative": hemispheres[1]}
        coordinates[prefix] = entry
        for part, widget in (("hemisphere", hemisphere), ("degrees", degrees),
                             ("minutes", minutes), ("seconds", seconds)):
            widget.setObjectName(prefix + "." + part)
            if part != "hemisphere":
                widget.lineEdit().textEdited.connect(
                    lambda text, entry=entry, part=part: entry.setdefault("typed", {}).__setitem__(part, text))
            row.addWidget(widget)
        row.addStretch(1)
        coordinates_form.addRow(label, row)

        def changed(_value, entry=entry, part="degrees"):
            entry["dirty"] = True

        degrees.valueChanged.connect(changed)
        for part, signal in (("minutes", minutes.valueChanged),
                             ("seconds", seconds.valueChanged),
                             ("hemisphere", hemisphere.currentTextChanged)):
            signal.connect(lambda value, part=part, changed=changed: changed(value, part=part))
    form_row.addLayout(form)
    form_row.addStretch(1)
    form_row.addLayout(coordinates_form)
    site_layout.addLayout(form_row)
    site_help = QLabel("<b>Site</b> applies to all subsequent measurements. <b>Edit</b> enables profile changes. <b>Save</b> stores changes and the default. Save new sites before use.")
    site_help.setWordWrap(True)
    site_layout.addWidget(site_help)

    def log(message):
        append_log = context.get("append_log")
        if callable(append_log):
            append_log(message)

    log(f"Configuration version {SCHEMA_VERSION}: {config_path()}")
    save = QPushButton("Save")
    reload_button = QPushButton("Reload")
    action_row = QHBoxLayout()
    action_row.addStretch(1)
    action_row.addWidget(save)
    action_row.addWidget(reload_button)
    layout.addLayout(action_row)
    layout.addStretch(1)
    state = {"data": defaults(), "draft": defaults(), "selected": "primary", "loading": False}

    def capture_site():
        if state["loading"]:
            return
        site = next(s for s in state["draft"]["sites"] if s["id"] == state["selected"])
        site["name"] = edits["site", "name"].text().strip()
        for prefix, entry in coordinates.items():
            for part, text in entry.get("typed", {}).items():
                widget = entry[part]
                base = QDoubleSpinBox if part == "seconds" else QSpinBox
                if base.validate(widget, text, len(text))[0] != QValidator.State.Acceptable:
                    raise ConfigError(f"Invalid {prefix} input: {text!r}.")
            if entry["dirty"]:
                degrees = entry["degrees"].value()
                if degrees < 0 and (entry["minutes"].value() or entry["seconds"].value()):
                    raise ConfigError(f"{prefix.capitalize()}: degrees are required when minutes or seconds are entered.")
                sign = -1 if entry["hemisphere"].currentText() == entry["negative"] else 1
                site[prefix + "_deg"] = None if degrees < 0 else sign * (degrees + entry["minutes"].value() / 60 + entry["seconds"].value() / 3600)

    def refresh_selectors():
        state["loading"] = True
        site_selector.blockSignals(True)
        site_selector.clear()
        for site in state["draft"]["sites"]:
            name = site["name"] or "Unnamed site"
            if site["id"] == state["draft"]["default_site_id"]:
                name += " (default)"
            site_selector.addItem(name, site["id"])
        site_selector.setCurrentIndex(site_selector.findData(state["selected"]))
        site_selector.blockSignals(False)
        delete_site.setEnabled(len(state["draft"]["sites"]) > 1)
        state["loading"] = False

    def enable_edit(enabled):
        edits["site", "name"].setReadOnly(not enabled)
        for entry in coordinates.values():
            for part in ("hemisphere", "degrees", "minutes", "seconds"):
                entry[part].setEnabled(enabled)

    def activate_site():
        global _session_site_id
        if any(s["id"] == state["selected"] for s in state["data"]["sites"]):
            _session_site_id = state["selected"]
        changed = context.get("config_changed")
        if callable(changed):
            changed()

    def show_site():
        enable_edit(False)
        state["loading"] = True
        site = next(s for s in state["draft"]["sites"] if s["id"] == state["selected"])
        edits["site", "name"].setText(site["name"])
        for prefix, entry in coordinates.items():
            for part in ("hemisphere", "degrees", "minutes", "seconds"):
                entry[part].blockSignals(True)
            value = site[prefix + "_deg"]
            ticks = round(abs(value or 0) * 36000)
            degrees_value, remainder = divmod(ticks, 36000)
            minutes_value, seconds_ticks = divmod(remainder, 600)
            entry["minutes"].setMaximum(59)
            entry["seconds"].setMaximum(59.9)
            entry["degrees"].setValue(-1 if value is None else degrees_value)
            entry["hemisphere"].setCurrentIndex(1 if value is not None and value < 0 else 0)
            entry["minutes"].setValue(minutes_value)
            entry["seconds"].setValue(seconds_ticks / 10)
            for part in ("hemisphere", "degrees", "minutes", "seconds"):
                entry[part].blockSignals(False)
            entry["dirty"] = False
            entry["typed"] = {}
        state["loading"] = False

    def select_site(_index):
        if state["loading"]:
            return
        try:
            capture_site()
        except ConfigError as exc:
            log(f"WARNING: Configuration: {exc}")
            refresh_selectors()
            return
        state["selected"] = site_selector.currentData()
        refresh_selectors()
        show_site()
        activate_site()

    def add():
        try:
            capture_site()
        except ConfigError as exc:
            log(f"WARNING: Configuration: {exc}")
            return
        names = {s["name"].casefold() for s in state["draft"]["sites"]}
        n = 1
        while f"Site {n}".casefold() in names:
            n += 1
        site = {"id": uuid.uuid4().hex, "name": f"Site {n}", "latitude_deg": None, "longitude_deg": None}
        state.setdefault("new_names", {})[site["id"]] = site["name"]
        state["draft"]["sites"].append(site)
        state["selected"] = site["id"]
        refresh_selectors()
        show_site()
        enable_edit(True)
        edits["site", "name"].setFocus()
        edits["site", "name"].selectAll()

    def delete():
        if len(state["draft"]["sites"]) <= 1:
            return
        if state["selected"] == state["draft"]["default_site_id"]:
            log("WARNING: Select another default site before deleting this site.")
            return
        state["draft"]["sites"] = [s for s in state["draft"]["sites"] if s["id"] != state["selected"]]
        state["selected"] = state["draft"]["default_site_id"]
        refresh_selectors()
        show_site()
        activate_site()

    def set_default():
        state["draft"]["default_site_id"] = state["selected"]
        refresh_selectors()

    def rename_site():
        if state["loading"]:
            return
        site = next(s for s in state["draft"]["sites"] if s["id"] == state["selected"])
        site["name"] = edits["site", "name"].text().strip()
        refresh_selectors()

    edits["site", "name"].editingFinished.connect(rename_site)
    site_selector.currentIndexChanged.connect(select_site)
    edit_site.clicked.connect(lambda: enable_edit(True))
    default_button.clicked.connect(set_default)
    add_site.clicked.connect(add)
    delete_site.clicked.connect(delete)

    def reload(preserve_selection=False):
        try:
            if config_path().exists():
                state["data"], messages = read_current_config(config_path())
                for message in messages:
                    log(message)
            else:
                state["data"] = defaults()
            state["draft"] = copy.deepcopy(state["data"])
            state["new_names"] = {}
            if not preserve_selection or not any(s["id"] == state["selected"] for s in state["data"]["sites"]):
                state["selected"] = state["data"]["default_site_id"]
            for (section, key), edit in edits.items():
                if section != "site":
                    value = state["data"][section]
                    value = value.get(key, "") if isinstance(value, dict) else ""
                    edit.setText(value if isinstance(value, str) else "")
                    if section == "bav":
                        state["bav_display"] = edit.text()
            vsx_limit.setValue(state["data"]["vsx"]["limit_magnitude"])
            refresh_selectors()
            show_site()
            activate_site()
            log("Configuration loaded.")
            save.setEnabled(True)
        except (OSError, ValueError) as exc:
            save.setEnabled(False)
            log(f"WARNING: Configuration: {exc}")

    def persist():
        try:
            capture_site()
            data = copy.deepcopy(state["draft"])
            for (section, key), edit in edits.items():
                if section == "bav":
                    if edit.text() != state["bav_display"]:
                        data["bav"] = validate_bav_section({key: edit.text()})
                elif section != "site":
                    data[section][key] = edit.text().strip()
            if not vsx_limit.hasAcceptableInput():
                raise ConfigError("Invalid VSX query limit.")
            vsx_limit.interpretText()
            data["vsx"]["limit_magnitude"] = vsx_limit.value()
            empty_ids = {site["id"] for site in data["sites"]
                         if site["id"] in state.get("new_names", {})
                         and site["name"] in ("", state["new_names"][site["id"]])
                         and site["latitude_deg"] is None and site["longitude_deg"] is None}
            data["sites"] = [site for site in data["sites"] if site["id"] not in empty_ids]
            if data["default_site_id"] in empty_ids:
                data["default_site_id"] = state["data"]["default_site_id"]
            save_config(data, expected=state["data"])
            reload(preserve_selection=True)
            log("Configuration saved.")
        except (OSError, ValueError) as exc:
            log(f"WARNING: Configuration: {exc}")

    save.clicked.connect(persist)
    reload_button.clicked.connect(lambda: reload())
    reload()
    return tab


EXPORT_SITE_KEYS = ("OBSERVER_SITE", "OBSERVER_LATITUDE", "OBSERVER_LONGITUDE")
EXPORT_SITE_TOLERANCE_DEG = 1 / 3600  # One arcsecond per coordinate.


def export_sites_differ(result: dict, current: dict) -> bool:
    """Compare site names and decimal coordinates, allowing display rounding."""
    if str(result.get("OBSERVER_SITE", "")).strip().casefold() != str(current.get("OBSERVER_SITE", "")).strip().casefold():
        return True
    for key, limit in (("OBSERVER_LATITUDE", 90), ("OBSERVER_LONGITUDE", 180)):
        left, right = str(result.get(key, "")).strip(), str(current.get(key, "")).strip()
        if not left or not right:
            if left != right:
                return True
            continue
        try:
            a, b = float(left), float(right)
        except ValueError:
            return True
        if not math.isfinite(a) or not math.isfinite(b) or abs(a) > limit or abs(b) > limit:
            return True
        difference = abs(a - b)
        if key == "OBSERVER_LONGITUDE":
            difference = min(difference, 360 - difference)
        if difference > EXPORT_SITE_TOLERANCE_DEG + 1e-12:
            return True
    return False


def select_export_site(parent, metadata: dict, current: dict, *, export_name: str, append_log=None) -> dict | None:
    """Confirm the result site on mismatch; never substitute the Config site."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox

    result_site = {key: str(metadata.get(key, "")).strip() for key in EXPORT_SITE_KEYS}
    current_site = {key: str(current.get(key, "")).strip() for key in EXPORT_SITE_KEYS}
    selected = result_site
    if export_sites_differ(result_site, current_site):
        german = export_name == "BAV"
        latitude_label = "Breite" if german else "Latitude"
        longitude_label = "Länge" if german else "Longitude"
        result_label = "Ergebnisstandort" if german else "Result site"
        current_label = "Aktueller Standort" if german else "Current site"
        accept_label = "Ergebnisstandort verwenden" if german else "Use result site"
        cancel_label = "Abbrechen" if german else "Cancel"

        def description(site):
            return (f"{site['OBSERVER_SITE'] or '—'}\n"
                    f"{latitude_label}: {site['OBSERVER_LATITUDE'] or '—'}°; "
                    f"{longitude_label}: {site['OBSERVER_LONGITUDE'] or '—'}°")

        dialog = QMessageBox(parent)
        dialog.setWindowTitle(f"{export_name} — {'Standort' if german else 'Site'}")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("Ergebnisstandort und aktueller Standort unterscheiden sich."
                       if german else "Result site and current site differ.")
        dialog.setInformativeText(
            f"{result_label}:\n{description(result_site)}\n\n"
            f"{current_label}:\n{description(current_site)}\n\n"
            + ("Mit dem gespeicherten Ergebnisstandort exportieren?\n"
               "Das gespeicherte Ergebnis und Config bleiben unverändert."
               if german else "Export using the saved result site?\n"
               "The saved result and Config remain unchanged."))
        result_button = dialog.addButton(accept_label, QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton(cancel_label, QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(result_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() is not result_button:
            if callable(append_log):
                append_log(f"{export_name} export cancelled during result site confirmation.")
            return None
    if callable(append_log):
        append_log(f"{export_name} export site from result: "
                   f"{selected['OBSERVER_SITE'] or '—'}; "
                   f"latitude={selected['OBSERVER_LATITUDE'] or '—'}, "
                   f"longitude={selected['OBSERVER_LONGITUDE'] or '—'}.")
    return selected


def bav_settings_with_export_site(settings: dict, site: dict) -> dict:
    """Replace only the site in BAV settings, keeping global observer values."""
    profile = defaults()
    target = default_site(profile)
    target['name'] = site['OBSERVER_SITE']
    for prefix, key in (("latitude", "OBSERVER_LATITUDE"), ("longitude", "OBSERVER_LONGITUDE")):
        value = site[key]
        target[prefix + '_deg'] = None if not value else float(value)
    projected = bav_settings(profile)
    result = dict(settings)
    for key in ('site_name', *(f'{prefix}_{part}' for prefix in ('latitude', 'longitude')
                             for part in ('hemisphere', 'degrees', 'minutes', 'seconds'))):
        result[key] = projected[key]
    # validate_config regenerates decimal coordinates from the selected DMS.
    for key in ('latitude', 'longitude'):
        result.pop(key, None)
    return result
