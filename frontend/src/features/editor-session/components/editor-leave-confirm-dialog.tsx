import { Button, Callout, Dialog, Flex, Text } from "@radix-ui/themes";
import { AlertCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useEditorSessionStore } from "../lib/editor-session-store";

export function EditorLeaveConfirmDialog() {
  const { t } = useTranslation();
  const dialogState = useEditorSessionStore((state) => state.dialogState);
  const chooseDecision = useEditorSessionStore((state) => state.chooseDecision);

  if (!dialogState || !dialogState.isOpen) {
    return null;
  }

  const { documents, isProcessing, errorMessage } = dialogState;

  const handleOpenChange = (open: boolean) => {
    if (!open && !isProcessing) {
      chooseDecision("cancel");
    }
  };

  return (
    <Dialog.Root
      open={dialogState.isOpen}
      onOpenChange={handleOpenChange}
    >
      <Dialog.Content
        maxWidth="440px"
        onEscapeKeyDown={(e) => {
          if (isProcessing) e.preventDefault();
        }}
        onPointerDownOutside={(e) => {
          if (isProcessing) e.preventDefault();
        }}
      >
        <Dialog.Title size="4">
          {t("editorSession.unsavedChangesTitle")}
        </Dialog.Title>

        <Dialog.Description size="2" mb="3">
          <Text color="gray">
            {t("editorSession.unsavedChangesDescription")}
          </Text>
        </Dialog.Description>

        <ul
          style={{
            margin: "0 0 16px 0",
            paddingLeft: "20px",
            maxHeight: "160px",
            overflowY: "auto",
            listStyleType: "disc",
          }}
        >
          {documents.map((doc) => (
            <li key={doc.documentKey} style={{ marginBottom: "4px" }}>
              <Text size="2" weight="medium">
                {doc.title || doc.entityId}
              </Text>
            </li>
          ))}
        </ul>

        {errorMessage ? (
          <Callout.Root color="red" size="1" mb="3">
            <Callout.Icon>
              <AlertCircle size={14} />
            </Callout.Icon>
            <Callout.Text>{errorMessage}</Callout.Text>
          </Callout.Root>
        ) : null}

        <Flex gap="2" justify="end" mt="4">
          <Button
            variant="outline"
            color="gray"
            disabled={isProcessing}
            onClick={() => chooseDecision("cancel")}
          >
            {t("editorSession.cancel")}
          </Button>

          <Button
            variant="soft"
            color="red"
            disabled={isProcessing}
            onClick={() => chooseDecision("discard")}
          >
            {t("editorSession.discard")}
          </Button>

          <Button
            variant="solid"
            loading={isProcessing}
            onClick={() => chooseDecision("save")}
          >
            {isProcessing ? t("editorSession.saving") : t("editorSession.saveAndLeave")}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
