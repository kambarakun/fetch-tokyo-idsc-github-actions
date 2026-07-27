#!/usr/bin/env bash

append_auto_merge_blocker() {
  local blocker="$1"

  if [ -z "$AUTO_MERGE_BLOCKERS" ]; then
    AUTO_MERGE_BLOCKERS="$blocker"
  else
    AUTO_MERGE_BLOCKERS="${AUTO_MERGE_BLOCKERS},${blocker}"
  fi
}

evaluate_auto_merge_gate() {
  AUTO_MERGE_REQUESTED="${AUTO_MERGE:-false}"
  FORCE_MERGE="${FORCE_MERGE_ON_FAILURE:-false}"
  FETCH_GATE_STATUS="${FETCH_STATUS:-unknown}"
  PROCESS_GATE_STATUS="${PROCESS_RESULT:-unknown}"
  CONTINUITY_GATE_STATUS="not_applicable"
  AUTO_MERGE_BLOCKERS=""

  case "$WORKFLOW_NAME" in
    fetch-data | fetch-data-daily | fetch-data-weekly)
      if [ "$FETCH_GATE_STATUS" != "success" ]; then
        append_auto_merge_blocker "fetch"
      fi
      case "$PROCESS_GATE_STATUS" in
        success | skipped) ;;
        *) append_auto_merge_blocker "process" ;;
      esac
      if [ "${VERIFY_CONTINUITY:-false}" = "true" ]; then
        case "${CONTINUITY_VALID:-}" in
          true) CONTINUITY_GATE_STATUS="passed" ;;
          false)
            CONTINUITY_GATE_STATUS="failed"
            append_auto_merge_blocker "continuity"
            ;;
          *)
            CONTINUITY_GATE_STATUS="unknown"
            append_auto_merge_blocker "continuity"
            ;;
        esac
      else
        CONTINUITY_GATE_STATUS="not_requested"
      fi
      ;;
    process-data)
      FETCH_GATE_STATUS="not_applicable"
      if [ "$PROCESS_GATE_STATUS" != "success" ]; then
        append_auto_merge_blocker "process"
      fi
      ;;
    migrate-metadata)
      FETCH_GATE_STATUS="not_applicable"
      PROCESS_GATE_STATUS="not_applicable"
      ;;
    *)
      append_auto_merge_blocker "workflow"
      ;;
  esac

  case "$WORKFLOW_NAME" in
    fetch-data-daily)
      if [ "${VALIDATION_BEFORE_SUCCESS:-}" = "true" ]; then
        VALIDATION_GATE_STATUS="passed"
      else
        VALIDATION_GATE_STATUS="failed"
        append_auto_merge_blocker "validation"
      fi
      ;;
    fetch-data-weekly)
      if [ "${VALIDATION_BEFORE_SUCCESS:-}" = "true" ] && [ "${VALIDATION_SUCCESS:-}" = "true" ]; then
        VALIDATION_GATE_STATUS="passed"
      else
        VALIDATION_GATE_STATUS="failed"
        append_auto_merge_blocker "validation"
      fi
      ;;
    fetch-data | migrate-metadata)
      VALIDATION_GATE_STATUS="not_requested"
      ;;
    process-data)
      if [ "${VERIFY_OUTPUT:-false}" != "true" ]; then
        VALIDATION_GATE_STATUS="not_requested"
      elif [ "${VALIDATION_PASSED:-}" = "true" ]; then
        VALIDATION_GATE_STATUS="passed"
      else
        VALIDATION_GATE_STATUS="failed"
        append_auto_merge_blocker "validation"
      fi
      ;;
    *)
      VALIDATION_GATE_STATUS="unknown"
      append_auto_merge_blocker "validation"
      ;;
  esac

  if [ -z "$AUTO_MERGE_BLOCKERS" ]; then
    AUTO_MERGE_BLOCKERS="none"
  fi

  AUTO_MERGE_EFFECTIVE="false"
  AUTO_MERGE_OVERRIDE_USED="false"
  if [ "$AUTO_MERGE_REQUESTED" != "true" ]; then
    AUTO_MERGE_GATE_STATUS="not_requested"
  elif [ "$AUTO_MERGE_BLOCKERS" = "none" ]; then
    AUTO_MERGE_EFFECTIVE="true"
    AUTO_MERGE_GATE_STATUS="passed"
  elif [ "$FORCE_MERGE" = "true" ]; then
    AUTO_MERGE_EFFECTIVE="true"
    AUTO_MERGE_GATE_STATUS="overridden"
    AUTO_MERGE_OVERRIDE_USED="true"
  else
    AUTO_MERGE_GATE_STATUS="blocked"
  fi

  : "$AUTO_MERGE_EFFECTIVE" "$AUTO_MERGE_GATE_STATUS" "$AUTO_MERGE_OVERRIDE_USED" "$VALIDATION_GATE_STATUS" \
    "$CONTINUITY_GATE_STATUS"
}

write_auto_merge_gate_env() {
  if [ -z "${GITHUB_ENV:-}" ]; then
    return
  fi

  AUTO_MERGE_GATE_EVALUATED="true"
  {
    echo "AUTO_MERGE_GATE_EVALUATED=$AUTO_MERGE_GATE_EVALUATED"
    echo "AUTO_MERGE_REQUESTED=$AUTO_MERGE_REQUESTED"
    echo "FORCE_MERGE=$FORCE_MERGE"
    echo "FETCH_GATE_STATUS=$FETCH_GATE_STATUS"
    echo "PROCESS_GATE_STATUS=$PROCESS_GATE_STATUS"
    echo "VALIDATION_GATE_STATUS=$VALIDATION_GATE_STATUS"
    echo "CONTINUITY_GATE_STATUS=$CONTINUITY_GATE_STATUS"
    echo "AUTO_MERGE_GATE_STATUS=$AUTO_MERGE_GATE_STATUS"
    echo "AUTO_MERGE_BLOCKERS=$AUTO_MERGE_BLOCKERS"
    echo "AUTO_MERGE_OVERRIDE_USED=$AUTO_MERGE_OVERRIDE_USED"
    echo "AUTO_MERGE_EFFECTIVE=$AUTO_MERGE_EFFECTIVE"
  } >> "$GITHUB_ENV"
}
