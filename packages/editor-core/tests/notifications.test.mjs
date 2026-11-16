import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dismissNotification,notificationSnapshot,notify,reportError,subscribeNotifications} from '../web/src/notifications.ts';
test('navigation cancellation is silent; actual failures remain visible',()=>{
 dismissNotification();reportError(new DOMException('Workspace changed','AbortError'));
 assert.equal(notificationSnapshot(),null);
 reportError(new DOMException('Connection timed out','TimeoutError'));
 assert.equal(notificationSnapshot().tone,'error');
 assert.equal(notificationSnapshot().message,'Connection timed out');
 dismissNotification();
});
test('duplicate polling failures do not repeatedly replace the same toast',()=>{
 let updates=0;const unsubscribe=subscribeNotifications(()=>updates++);
 reportError(new Error('Unavailable'));const id=notificationSnapshot().id;
 reportError(new Error('Unavailable'));assert.equal(updates,1);assert.equal(notificationSnapshot().id,id);
 notify('Guide updated');assert.equal(updates,2);assert.equal(notificationSnapshot().tone,'info');
 dismissNotification();assert.equal(notificationSnapshot(),null);unsubscribe();
});
