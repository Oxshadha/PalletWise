import { useState } from "react";

import { type Conversation, fetchConversation, fetchConversations } from "../../shared/api/client";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";

/** Conversation history for this user and site (UX spec 6, FR-COP-11). */
export function HistoryMenu({ onOpened }: { onOpened: () => void }) {
  const { dispatch, siteId } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [failed, setFailed] = useState(false);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      setFailed(false);
      fetchConversations(siteId)
        .then(setItems)
        .catch(() => {
          setFailed(true);
        });
    }
  };
  const openConversation = (id: string) => {
    fetchConversation(siteId, id)
      .then((detail) => {
        dispatch({ type: "conversation-loaded", conversationId: detail.id, runs: detail.runs });
        setOpen(false);
        onOpened();
      })
      .catch(() => {
        setFailed(true);
      });
  };

  return (
    <div className="pw-history">
      <button type="button" className="pw-button-quiet" aria-expanded={open} onClick={toggle}>
        <Icon name="history" size={16} /> History
      </button>
      {open && (
        <div className="pw-history-list">
          {failed && <p role="alert">History could not be loaded.</p>}
          {items?.length === 0 && <p className="pw-muted">No earlier conversations.</p>}
          {items && items.length > 0 && (
            <ul>
              {items.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className="pw-link-button"
                    onClick={() => {
                      openConversation(c.id);
                    }}
                  >
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
