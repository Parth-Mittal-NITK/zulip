import calendar
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.http import HttpRequest
from django.utils import translation
from two_factor.utils import default_device

from zerver.context_processors import get_apps_page_url
from zerver.lib.events import do_events_register
from zerver.lib.i18n import (
    get_and_set_request_language,
    get_language_list,
    get_language_translation_data,
)
from zerver.lib.request import get_request_notes
from zerver.models import Message, Realm, Stream, UserProfile
from zerver.views.message_flags import get_latest_update_message_flag_activity


@dataclass
class BillingInfo:
    show_billing: bool
    show_plans: bool


@dataclass
class UserPermissionInfo:
    color_scheme: int
    is_guest: bool
    is_realm_admin: bool
    is_realm_owner: bool
    show_webathena: bool


def get_furthest_read_time(user_profile: Optional[UserProfile]) -> Optional[float]:
    if user_profile is None:
        return time.time()

    user_activity = get_latest_update_message_flag_activity(user_profile)
    if user_activity is None:
        return None

    return calendar.timegm(user_activity.last_visit.utctimetuple())


def get_bot_types(user_profile: Optional[UserProfile]) -> List[Dict[str, object]]:
    bot_types: List[Dict[str, object]] = []
    if user_profile is None:
        return bot_types

    for type_id, name in UserProfile.BOT_TYPES.items():
        bot_types.append(
            dict(
                type_id=type_id,
                name=name,
                allowed=type_id in user_profile.allowed_bot_types,
            )
        )
    return bot_types


def promote_sponsoring_zulip_in_realm(realm: Realm) -> bool:
    if not settings.PROMOTE_SPONSORING_ZULIP:
        return False

    # If PROMOTE_SPONSORING_ZULIP is enabled, advertise sponsoring
    # Zulip in the gear menu of non-paying organizations.
    return realm.plan_type in [Realm.STANDARD_FREE, Realm.SELF_HOSTED]


def get_billing_info(user_profile: Optional[UserProfile]) -> BillingInfo:
    show_billing = False
    show_plans = False
    if settings.CORPORATE_ENABLED and user_profile is not None:
        if user_profile.has_billing_access:
            from corporate.models import CustomerPlan, get_customer_by_realm

            customer = get_customer_by_realm(user_profile.realm)
            if customer is not None:
                if customer.sponsorship_pending:
                    show_billing = True
                elif CustomerPlan.objects.filter(customer=customer).exists():
                    show_billing = True

        if not user_profile.is_guest and user_profile.realm.plan_type == Realm.LIMITED:
            show_plans = True

    return BillingInfo(
        show_billing=show_billing,
        show_plans=show_plans,
    )


def get_user_permission_info(user_profile: Optional[UserProfile]) -> UserPermissionInfo:
    if user_profile is not None:
        return UserPermissionInfo(
            color_scheme=user_profile.color_scheme,
            is_guest=user_profile.is_guest,
            is_realm_owner=user_profile.is_realm_owner,
            is_realm_admin=user_profile.is_realm_admin,
            show_webathena=user_profile.realm.webathena_enabled,
        )
    else:
        return UserPermissionInfo(
            color_scheme=UserProfile.COLOR_SCHEME_AUTOMATIC,
            is_guest=False,
            is_realm_admin=False,
            is_realm_owner=False,
            show_webathena=False,
        )


def build_page_params_for_home_page_load(
    request: HttpRequest,
    user_profile: Optional[UserProfile],
    realm: Realm,
    insecure_desktop_app: bool,
    narrow: List[List[str]],
    narrow_stream: Optional[Stream],
    narrow_topic: Optional[str],
    first_in_realm: bool,
    prompt_for_invites: bool,
    needs_tutorial: bool,
) -> Tuple[int, Dict[str, Any]]:
    """
    This function computes page_params for when we load the home page.

    The page_params data structure gets sent to the client.
    """
    client_capabilities = {
        "notification_settings_null": True,
        "bulk_message_deletion": True,
        "user_avatar_url_field_optional": True,
        "stream_typing_notifications": False,  # Set this to True when frontend support is implemented.
        "user_settings_object": True,
    }

    if user_profile is not None:
        client = get_request_notes(request).client
        assert client is not None
        register_ret = do_events_register(
            user_profile,
            client,
            apply_markdown=True,
            client_gravatar=True,
            slim_presence=True,
            client_capabilities=client_capabilities,
            narrow=narrow,
            include_streams=False,
        )
    else:
        # Since events for spectator is not implemented, we only fetch the data
        # at the time of request and don't register for any events.
        # TODO: Implement events for spectator.
        from zerver.lib.events import fetch_initial_state_data, post_process_state

        register_ret = fetch_initial_state_data(
            user_profile,
            realm=realm,
            event_types=None,
            queue_id=None,
            client_gravatar=False,
            user_avatar_url_field_optional=client_capabilities["user_avatar_url_field_optional"],
            user_settings_object=client_capabilities["user_settings_object"],
            slim_presence=False,
            include_subscribers=False,
            include_streams=False,
        )

        post_process_state(user_profile, register_ret, False)

    furthest_read_time = get_furthest_read_time(user_profile)

    request_language = get_and_set_request_language(
        request,
        register_ret["user_settings"]["default_language"],
        translation.get_language_from_path(request.path_info),
    )

    two_fa_enabled = settings.TWO_FACTOR_AUTHENTICATION_ENABLED and user_profile is not None
    billing_info = get_billing_info(user_profile)
    user_permission_info = get_user_permission_info(user_profile)

    # Pass parameters to the client-side JavaScript code.
    # These end up in a JavaScript Object named 'page_params'.
    page_params = dict(
        ## Server settings.
        test_suite=settings.TEST_SUITE,
        insecure_desktop_app=insecure_desktop_app,
        login_page=settings.HOME_NOT_LOGGED_IN,
        warn_no_email=settings.WARN_NO_EMAIL,
        search_pills_enabled=settings.SEARCH_PILLS_ENABLED,
        # Only show marketing email settings if on Zulip Cloud
        corporate_enabled=settings.CORPORATE_ENABLED,
        ## Misc. extra data.
        language_list=get_language_list(),
        needs_tutorial=needs_tutorial,
        first_in_realm=first_in_realm,
        prompt_for_invites=prompt_for_invites,
        furthest_read_time=furthest_read_time,
        bot_types=get_bot_types(user_profile),
        two_fa_enabled=two_fa_enabled,
        apps_page_url=get_apps_page_url(),
        show_billing=billing_info.show_billing,
        promote_sponsoring_zulip=promote_sponsoring_zulip_in_realm(realm),
        show_plans=billing_info.show_plans,
        show_webathena=user_permission_info.show_webathena,
        # Adding two_fa_enabled as condition saves us 3 queries when
        # 2FA is not enabled.
        two_fa_enabled_user=two_fa_enabled and bool(default_device(user_profile)),
        is_spectator=user_profile is None,
        # There is no event queue for spectators since
        # events support for spectators is not implemented yet.
        no_event_queue=user_profile is None,
    )

    for field_name in register_ret.keys():
        page_params[field_name] = register_ret[field_name]

    if narrow_stream is not None:
        # In narrow_stream context, initial pointer is just latest message
        recipient = narrow_stream.recipient
        try:
            max_message_id = (
                Message.objects.filter(recipient=recipient).order_by("id").reverse()[0].id
            )
        except IndexError:
            max_message_id = -1
        page_params["narrow_stream"] = narrow_stream.name
        if narrow_topic is not None:
            page_params["narrow_topic"] = narrow_topic
        page_params["narrow"] = [dict(operator=term[0], operand=term[1]) for term in narrow]
        page_params["max_message_id"] = max_message_id
        assert isinstance(page_params["user_settings"], dict)
        page_params["user_settings"]["enable_desktop_notifications"] = False

    page_params["translation_data"] = get_language_translation_data(request_language)

    return register_ret["queue_id"], page_params
