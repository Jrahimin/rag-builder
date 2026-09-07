import type { ChangeEvent, ComponentProps } from "react";

/** Native date pickers may emit input before change/blur; keep the draft current. */
export function SourceDateInput(props: ComponentProps<"input">) {
  return (
    <input
      {...props}
      type="date"
      onInput={(event) => {
        props.onInput?.(event);
        props.onChange?.(event as unknown as ChangeEvent<HTMLInputElement>);
      }}
    />
  );
}
