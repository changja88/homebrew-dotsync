import test from "node:test";
import assert from "node:assert/strict";

import { confirmationDialog } from "../../../lib/dotsync/web/static/render.mjs";


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

  const pending = confirmationDialog({
    title: "Apply를 실행할까요?",
    copy: "백업 후 실행합니다.",
    confirmText: "Apply",
    danger: true,
  });

  assert.equal(elements["#confirmation-dialog"].returnValue, "cancel");
  closeListener();
  assert.deepEqual(await pending, { confirmed: false, value: "" });
});
