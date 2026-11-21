import { Fragment, type ReactNode } from "react";

/* Identifiers and figures a reader scans for: SKUs, order numbers, bin codes, zones, numbers
   with an optional unit. Bolded so an answer can be read at a glance. */
const KEY = String.raw`\b(?:SKU-[\w-]+|SO-[\w-]+|PO-[\w-]+|[A-Z]\d+-\d{2}-\d{2}|Zone [A-Z]\b)|(?<![\w.-])\d[\d,]*(?:\.\d+)?(?:\s?%|\s(?:units?|days?|hours?|orders?|locations?|bins?|LKR))?`;
const INLINE = new RegExp(String.raw`\*\*(.+?)\*\*|\*(.+?)\*|(${KEY})`, "g");

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    if (at > last) out.push(text.slice(last, at));
    const [whole, strong, emphasis, key] = match;
    if (strong !== undefined) out.push(<strong key={at}>{strong}</strong>);
    else if (emphasis !== undefined) out.push(<em key={at}>{emphasis}</em>);
    else out.push(<strong key={at}>{key ?? whole}</strong>);
    last = at + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/**
 * Answer text made readable without trusting it as markup: paragraphs on blank lines, lists from
 * lines that start with "- " or "* ", **bold** and *italic*, and key figures in bold. Everything is
 * a React text node, so nothing in the text can become HTML.
 */
export function RichText({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\s*\n/);
  return (
    <>
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((line) => line.trim() !== "");
        const bullet = /^\s*[-*•]\s+/;
        if (lines.length > 0 && lines.every((line) => bullet.test(line))) {
          return (
            <ul key={b} className="pw-rich-list">
              {lines.map((line, i) => (
                <li key={i}>{inline(line.replace(bullet, ""))}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={b} className="pw-rich-p">
            {lines.map((line, i) => (
              <Fragment key={i}>
                {i > 0 && <br />}
                {inline(line)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}
