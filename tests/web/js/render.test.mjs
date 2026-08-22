import test from "node:test";
import assert from "node:assert/strict";

import * as rendering from "../../../lib/dotsync/web/static/render.mjs";


test("a new confirmation defaults to cancel even after an earlier confirm", async () => {
  let closeListener = null;
  const elements = {
    "#confirmation-dialog": {
      returnValue: "confirm",
      showModal() {},
      addEventListener(name, listener) {
        assert.equal(name, "close");
        closeListener = listener;
      },
    },
    "#confirmation-title": { textContent: "" },
    "#confirmation-copy": { textContent: "" },
    "#confirmation-submit": { textContent: "", className: "" },
    "#confirmation-label": { textContent: "", hidden: false },
    "#confirmation-input": { value: "", hidden: false },
  };
  globalThis.document = {
    querySelector(selector) {
      return elements[selector];
    },
  };

  const pending = rendering.confirmationDialog({
    title: "Apply를 실행할까요?",
    copy: "백업 후 실행합니다.",
    confirmText: "Apply",
    danger: true,
  });

  assert.equal(elements["#confirmation-dialog"].returnValue, "cancel");
  closeListener();
  assert.deepEqual(await pending, { confirmed: false, value: "" });
});


test("preview rendering and confirmation use immutable preview direction", () => {
  assert.equal(typeof rendering.syncPreviewPresentation, "function");
  assert.equal(typeof rendering.syncExecutionConfirmation, "function");
  const preview = Object.freeze({
    direction: "apply",
    apps: Object.freeze(["zsh"]),
    digest: "a".repeat(64),
    plans: Object.freeze([]),
  });

  const presentation = rendering.syncPreviewPresentation({
    activeDirection: "backup",
    selectedApps: new Set(["ghostty"]),
    preview,
  });
  const confirmation = rendering.syncExecutionConfirmation(preview);

  assert.equal(presentation.direction, "apply");
  assert.equal(presentation.title, "Apply preview");
  assert.equal(presentation.executeText, "Apply 실행");
  assert.match(presentation.warning, /digest/);
  assert.match(presentation.warning, /selected sync folder\/\.backups/);
  assert.equal(confirmation.title, "Apply를 실행할까요?");
  assert.equal(confirmation.confirmText, "백업 후 Apply");
  assert.equal(confirmation.danger, true);
});
