import { cleanup, fireEvent, render, screen } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import AssistantComposer from "../AssistantComposer.vue";

describe("AssistantComposer", () => {
  afterEach(cleanup);
  it("does not submit Enter while an IME composition is active", async () => {
    const send = vi.fn();
    render(AssistantComposer, { props: { awaiting: false, modelValue: "测试问题", onSend: send } });
    const input = screen.getByRole("textbox", { name: "向企业总助手提问" });

    await fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(send).not.toHaveBeenCalled();
    await fireEvent.keyDown(input, { key: "Enter", isComposing: false });
    expect(send).toHaveBeenCalledWith("测试问题");
  });

  it("does not erase a controlled draft merely because send was requested", async () => {
    render(AssistantComposer, { props: { awaiting: false, modelValue: "保留这个问题" } });
    await fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(screen.getByRole("textbox", { name: "向企业总助手提问" })).toHaveValue("保留这个问题");
  });
});
