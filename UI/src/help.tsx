// Help in the suite header, as in Microsoft 365: getting started, keyboard shortcuts, feedback, and About with versions.
// The dialogs load on first open, so they stay out of the first download.
import { lazy, Suspense } from 'react';
import { Button, Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuDivider, Tooltip } from '@fluentui/react-components';
import { QuestionCircleRegular, KeyboardRegular, PersonFeedbackRegular, MailRegular, InfoRegular, RocketRegular } from '@fluentui/react-icons';
import { USERS } from './data';

const HelpDialogs = lazy(() => import('./dialogs').then((m) => ({ default: m.HelpDialogs })));
const ADMIN_EMAIL = USERS.find((u) => u.role === 'admin')?.email ?? '';
export type HelpDialog = 'start' | 'shortcuts' | 'feedback' | 'about';

export function HelpMenu({ className, open, setOpen }: { className?: string; open: HelpDialog | null; setOpen: (d: HelpDialog | null) => void }) {
  return (
    <>
      <Menu>
        <MenuTrigger disableButtonEnhancement>
          <Tooltip content="Help" relationship="label"><Button appearance="subtle" className={className} icon={<QuestionCircleRegular />} /></Tooltip>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            <MenuItem icon={<RocketRegular />} onClick={() => setOpen('start')}>Get started</MenuItem>
            <MenuItem icon={<KeyboardRegular />} secondaryContent="Shift+?" onClick={() => setOpen('shortcuts')}>Keyboard shortcuts</MenuItem>
            <MenuItem icon={<PersonFeedbackRegular />} onClick={() => setOpen('feedback')}>Give feedback</MenuItem>
            <MenuItem icon={<MailRegular />} onClick={() => { location.href = `mailto:${ADMIN_EMAIL}?subject=Document%20Extraction%20help`; }}>Email your administrator</MenuItem>
            <MenuDivider />
            <MenuItem icon={<InfoRegular />} onClick={() => setOpen('about')}>About Document Extraction</MenuItem>
          </MenuList>
        </MenuPopover>
      </Menu>
      {open && <Suspense fallback={null}><HelpDialogs open={open} close={() => setOpen(null)} /></Suspense>}
    </>
  );
}
