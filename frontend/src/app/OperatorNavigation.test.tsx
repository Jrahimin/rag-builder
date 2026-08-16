import { act, fireEvent, screen } from "@testing-library/react";
import { renderOperatorComponent } from "../test/renderOperatorComponent";
import { OperatorNavigation } from "./OperatorNavigation";

test("keeps the dock collapsed after the tab is hidden until the pointer leaves", () => {
  renderOperatorComponent(<OperatorNavigation />);
  const dock = screen.getByLabelText("Operator console").closest("aside");
  expect(dock).not.toHaveClass("sidebar--expanded");

  fireEvent.mouseEnter(dock!);
  expect(dock).toHaveClass("sidebar--expanded");

  act(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(dock).not.toHaveClass("sidebar--expanded");

  fireEvent.mouseEnter(dock!);
  expect(dock).not.toHaveClass("sidebar--expanded");

  fireEvent.mouseLeave(dock!);
  fireEvent.mouseEnter(dock!);
  expect(dock).toHaveClass("sidebar--expanded");
});
