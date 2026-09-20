DOMAIN := GTIPTVPlayerPro
PACKAGE_NAME := GT IPTV Player Pro
PACKAGE_VERSION := 1.1.0
BUGS_ADDRESS := https://github.com/VicTuS59/GT-IPTV-Player-Pro/issues
SOURCE_DATE_EPOCH ?= 1700000000
export SOURCE_DATE_EPOCH

PYTHON ?= python3
XGETTEXT ?= xgettext
PYGETTEXT ?= pygettext3
MSGMERGE ?= msgmerge
MSGATTRIB ?= msgattrib
MSGFMT ?= msgfmt
MSGCMP ?= msgcmp
CMP ?= cmp
GREP ?= grep

PLUGIN_ROOT := ../GTIPTVPlayerPro
LOCALE_ROOT := $(PLUGIN_ROOT)/locale
POT := $(DOMAIN).pot
FORMAT_TOOL := format_flags.py
LANGUAGES := ar bg ca cs da de el es et fa fi fr_FR fy gl he hr hu id is it ku lt lv mk nb nl nn pl pt ro ru sk sl sq_AL sr sv ta th tr uk vi zh zh_HK
PO_FILES := $(addsuffix .po,$(LANGUAGES))
PYTHON_SOURCES := $(sort $(wildcard $(PLUGIN_ROOT)/*.py))

.PHONY: all pot update compile check FORCE

all: check

pot: $(POT)

$(POT): FORCE $(PYTHON_SOURCES) $(FORMAT_TOOL) Makefile
	@set -e; \
	tmp="$@.tmp"; \
	if command -v "$(XGETTEXT)" >/dev/null 2>&1; then \
		TZ=UTC $(XGETTEXT) \
			--language=Python \
			--from-code=UTF-8 \
			--keyword=_ \
			--keyword=N_ \
			--add-location=full \
			--width=79 \
			--package-name="$(PACKAGE_NAME)" \
			--package-version="$(PACKAGE_VERSION)" \
			--msgid-bugs-address="$(BUGS_ADDRESS)" \
			--output="$$tmp" \
			$(PYTHON_SOURCES); \
	else \
		TZ=UTC $(PYGETTEXT) \
			-k N_ \
			-S GNU \
			-w 79 \
			-o "$$tmp" \
			$(PYTHON_SOURCES); \
	fi; \
	$(PYTHON) $(FORMAT_TOOL) --fix-template "$$tmp"; \
	mv "$$tmp" "$@"

update: pot
	@set -e; \
	for language in $(LANGUAGES); do \
		po="$$language.po"; \
		$(MSGMERGE) --quiet --update --backup=none "$$po" "$(POT)"; \
		tmp="$$po.tmp"; \
		$(MSGATTRIB) --no-obsolete --output-file="$$tmp" "$$po"; \
		mv "$$tmp" "$$po"; \
		$(PYTHON) $(FORMAT_TOOL) --fix "$$po"; \
	done

compile:
	@set -e; \
	for language in $(LANGUAGES); do \
		po="$$language.po"; \
		directory="$(LOCALE_ROOT)/$$language/LC_MESSAGES"; \
		catalog="$$directory/$(DOMAIN).mo"; \
		if $(GREP) -q '^#,.*fuzzy' "$$po"; then \
			echo "fuzzy translation is not release-ready: $$po" >&2; \
			exit 1; \
		fi; \
		mkdir -p "$$directory"; \
		$(MSGFMT) --check --check-format --check-header \
			--output-file="$$catalog.tmp" "$$po"; \
		mv "$$catalog.tmp" "$$catalog"; \
	done

check:
	@set -e; \
	generated="$$(mktemp)"; \
	expected="$$(mktemp)"; \
	trap 'rm -f "$$generated" "$$expected"' 0 1 2 3 15; \
	if command -v "$(XGETTEXT)" >/dev/null 2>&1; then \
		TZ=UTC $(XGETTEXT) \
			--language=Python \
			--from-code=UTF-8 \
			--keyword=_ \
			--keyword=N_ \
			--add-location=full \
			--width=79 \
			--package-name="$(PACKAGE_NAME)" \
			--package-version="$(PACKAGE_VERSION)" \
			--msgid-bugs-address="$(BUGS_ADDRESS)" \
			--output="$$generated" \
			$(PYTHON_SOURCES); \
	else \
		TZ=UTC $(PYGETTEXT) \
			-k N_ \
			-S GNU \
			-w 79 \
			-o "$$generated" \
			$(PYTHON_SOURCES); \
	fi; \
	$(PYTHON) $(FORMAT_TOOL) --fix-template "$$generated"; \
	$(MSGCMP) --no-fuzzy-matching --use-untranslated \
		"$$generated" "$(POT)"; \
	$(MSGCMP) --no-fuzzy-matching --use-untranslated \
		"$(POT)" "$$generated"; \
	$(PYTHON) $(FORMAT_TOOL) --check $(POT) $(PO_FILES); \
	for language in $(LANGUAGES); do \
		po="$$language.po"; \
		catalog="$(LOCALE_ROOT)/$$language/LC_MESSAGES/$(DOMAIN).mo"; \
		if $(GREP) -q '^#,.*fuzzy' "$$po"; then \
			echo "fuzzy translation is not release-ready: $$po" >&2; \
			exit 1; \
		fi; \
		$(MSGFMT) --check --check-format --check-header \
			--output-file=/dev/null "$$po"; \
		$(MSGCMP) --no-fuzzy-matching --use-untranslated \
			"$$po" "$(POT)"; \
		$(MSGCMP) --no-fuzzy-matching --use-untranslated \
			"$(POT)" "$$po"; \
		$(MSGFMT) --check --check-format --check-header \
			--output-file="$$expected" "$$po"; \
	$(CMP) -s "$$expected" "$$catalog" || { \
			echo "compiled catalog is out of date: $$catalog" >&2; \
			exit 1; \
		}; \
	done

FORCE:
