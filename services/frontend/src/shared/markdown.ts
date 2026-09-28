import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt({
  breaks: true,
  html: false,
  linkify: true,
  typographer: false,
});

const defaultLinkOpen = markdown.renderer.rules.link_open
  ?? ((tokens, index, options, _environment, renderer) => renderer.renderToken(tokens, index, options));

markdown.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
  const href = String(tokens[index].attrGet("href") ?? "");
  if (/^(?:https?:)?\/\//i.test(href)) {
    tokens[index].attrSet("target", "_blank");
    tokens[index].attrSet("rel", "noopener noreferrer");
  }
  return defaultLinkOpen(tokens, index, options, environment, renderer);
};

export function renderAssistantMarkdown(content: string): string {
  return DOMPurify.sanitize(markdown.render(content || ""), {
    ADD_ATTR: ["target"],
    ALLOW_UNKNOWN_PROTOCOLS: false,
    FORBID_ATTR: ["style"],
    FORBID_TAGS: ["button", "embed", "form", "iframe", "img", "input", "object", "style"],
    USE_PROFILES: { html: true },
  });
}
