//
//  AppCMSOTTDebugSnapshot.swift
//  AppCMS
//
//  DEBUG-only OTT state dump for Viewlift AI dashboard.
//  Covers guest, logged-in, TVE, and shared session/geo/config surfaces.
//

#if DEBUG
import Foundation
import UIKit
import ViewliftNetworkCapture
import VLPlayerLib

/// Bridges AppCMS runtime state into ViewliftNetworkCapture without leaking secrets.
///
/// ViewliftOne defines `typealias JSONValue = JSON` (SwiftyJSON). Always use
/// `CaptureJSON` (= `ViewliftNetworkCapture.JSONValue`) in this file.
final class AppCMSOTTDebugSnapshot: OTTDebugSnapshotProviding {
    private typealias CaptureJSON = ViewliftNetworkCapture.JSONValue

    static let shared = AppCMSOTTDebugSnapshot()

    private init() {}

    func ottDebugSnapshot() -> [String: ViewliftNetworkCapture.JSONValue] {
        #if os(tvOS)
        let platform = "tvOS"
        #else
        let platform = "iOS"
        #endif
        let page = pageSection()
        let player = playerSection()
        let entitlementsV2 = entitlementsV2Section()
        return [
            "provider": .string("AppCMS"),
            "platform": .string(platform),
            "flow": .object(flowSection()),
            "session": .object(sessionSection()),
            "user": .object(userSection()),
            "geo": .object(geoSection()),
            "parental": .object(parentalSection()),
            "subscription": .object(subscriptionSection()),
            "entitlements_v2": .object(entitlementsV2),
            "entitlement_cache": .object(entitlementCacheSection()),
            "plan_features": .object(planFeaturesSection()),
            "tve": .object(tveSection()),
            "profiles": .object(profilesSection()),
            "personalization": .object(personalizationSection()),
            "devices": .object(devicesSection()),
            "user_lists": .object(userListsSection()),
            "player": .object(player),
            "player_prefs": .object(playerPrefsSection()),
            "content_filter": .object(contentFilterSection()),
            "theme": .object(themeSection()),
            "config": .object(configSection()),
            "site_features": .object(siteFeaturesSection()),
            "drm": .object(drmSection()),
            "sso": .object(ssoSection()),
            "navigation": .object(navigationSection()),
            "navigation_catalog": .object(navigationCatalogSection()),
            "page": .object(page),
            "analytics": .object(analyticsSection()),
            "device": .object(deviceSection()),
            "spm_hint": .string(
                "Full SPM pin list is on the Mac dashboard (Package.resolved). Device lists linked frameworks."
            )
        ]
    }

    // MARK: - Flow (guest vs logged-in)

    private func flowSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        let loggedIn = user?.isUserLoggedIn == true
        let tve = user?.isUserLoggedInUsingTVE() == true
        let modes = Constants.shared.kAPPDELEGATE.userLoginModes.map(\.rawValue)
        let anonymousId = Utility.anonymousId ?? ""
        let hasUserObject = user != nil
        let flowName: String
        if tve {
            flowName = "tve"
        } else if loggedIn {
            flowName = "logged_in"
        } else if hasUserObject || !anonymousId.isEmpty {
            flowName = "guest"
        } else {
            flowName = "cold_start"
        }
        return [
            "name": .string(flowName),
            "is_guest": .bool(!loggedIn),
            "is_logged_in": .bool(loggedIn),
            "is_tve": .bool(tve),
            "login_modes": .array(modes.map(CaptureJSON.string)),
            "anonymous_id": .string(anonymousId),
            "has_user_object": .bool(hasUserObject),
            "ud_is_logged_in": .bool(udBool(.isLoggedIn)),
            "ud_is_subscribed": .bool(udBool(.isSubscribed)),
            "ud_login_type": .string(udString(.loginType)),
            "ud_user_id": .string(udString(.userId))
        ]
    }

    // MARK: - Session (tokens metadata only)

    private func sessionSection() -> [String: CaptureJSON] {
        let auth = udString(.authToken)
        let refresh = udString(.refreshToken)
        let tveAccess = udString(.accessTokenForTVE)
        return [
            "auth_token_present": .bool(!auth.isEmpty),
            "auth_token_length": .number(Double(auth.count)),
            "refresh_token_present": .bool(!refresh.isEmpty),
            "auth_token_expiry": jsonFromAny(UserDefaultsManager.shared.getSynchronously(forKey: .authTokenExpiry) as Any, depth: 0),
            "auth_token_date": jsonFromAny(UserDefaultsManager.shared.getSynchronously(forKey: .authTokenDate) as Any, depth: 0),
            "pin_verified_for_session": .bool(udBool(.pinVerifiedForSession)),
            "tve_access_token_present": .bool(!tveAccess.isEmpty),
            "tve_session_code_present": .bool(!udString(.sessionCodeForTVE).isEmpty),
            "token_values_exposed": .bool(false)
        ]
    }

    // MARK: - User

    private func userSection() -> [String: CaptureJSON] {
        guard let user = ParentalControlHelper.getUserDetails() else {
            return [
                "logged_in": .bool(false),
                "note": .string("No SFUserDetails in memory (typical cold guest before identity load)")
            ]
        }
        return [
            "logged_in": .bool(user.isUserLoggedIn),
            "user_id": .string(user.userID ?? ""),
            "upstream_user_id": .string(user.upstreamUserID ?? ""),
            "email": .string(redactEmail(user.emailID)),
            "email_verified": .bool(user.isVerifiedEmail),
            "unverified_email_present": .bool(!(user.unverifiedEmail ?? "").isEmpty),
            "name": .string(user.name ?? ""),
            "nickname": .string(user.nickname ?? ""),
            "mobile_present": .bool(!(user.mobile ?? "").isEmpty),
            "mobile_verified": .bool(user.isVerifiedPhone),
            "user_state": .string(user.userState ?? ""),
            "country": .string(user.country ?? ""),
            "country_code": .string(user.countryCode ?? ""),
            "zipcode": .string(user.zipcode ?? ""),
            "login_provider": .string(user.loginProvider ?? ""),
            "provider": .string(user.provider ?? ""),
            "providers": .array((user.providers ?? []).map(CaptureJSON.string)),
            "password_enabled": .bool(user.passwordEnabled ?? false),
            "email_consent": .bool(user.emailConsent ?? false),
            "is_vip": .bool(user.isVip),
            "is_existing_user": .bool(user.isExistingUser ?? false),
            "is_hba_enabled": .bool(user.isHbaEnabled ?? false),
            "is_personalized": .bool(user.isPersonalized),
            "default_profile_id": .string(user.defaultProfile?.profileId ?? ""),
            "picture_present": .bool(!(user.picture ?? "").isEmpty),
            "user_ip": .string(user.userIPAddress ?? ""),
            "registered_date": .number(user.registeredDate?.timeIntervalSince1970 ?? 0)
        ]
    }

    // MARK: - Geo

    private func geoSection() -> [String: CaptureJSON] {
        [
            "country_code": .string(udString(.countryCode)),
            "country_name": .string(udString(.countryName)),
            "city": .string(udString(.city)),
            "zip_code": .string(udString(.zipCode)),
            "subdivision": .string(udString(.subdivisionEnName)),
            "device_ip": .string(udString(.deviceIPAddress)),
            "lat_long_present": .bool(!udString(.userLatLong).isEmpty),
            "lat_long_zip_present": .bool(!udString(.latLongZipCode).isEmpty),
            "out_of_range_seen": .bool(udBool(.hasSeenOutOfRangeView)),
            "show_no_plans_screen": .bool(udBool(.toShowNoPlansScreen)),
            "language_code": .string(udString(.currentLanguageCode)),
            "language_name": .string(udString(.currentLanguageName))
        ]
    }

    // MARK: - Parental

    private func parentalSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        let pinSet = !(user?.parentalPin ?? "").isEmpty || !udString(.parentalPin).isEmpty
        return [
            "site_feature_enabled": .bool(AppConfiguration.shared.isParentalControlEnabled),
            "user_enabled": .bool(ParentalControlHelper.isParentalControlEnabledForUser()),
            "ud_parental_control": .bool(udBool(.parentalControl)),
            "rating": .string(ParentalControlHelper.fetchParentalRatingForUser() ?? ""),
            "pin_present": .bool(pinSet),
            "pin_verified_for_session": .bool(udBool(.pinVerifiedForSession)),
            "pin_value_exposed": .bool(false)
        ]
    }

    // MARK: - Subscription

    private func subscriptionSection() -> [String: CaptureJSON] {
        guard let user = ParentalControlHelper.getUserDetails() else {
            return [
                "is_subscribed": .bool(udBool(.isSubscribed)),
                "is_user_subscribed_effective": .bool(false),
                "note": .string("guest / no user object")
            ]
        }
        var purchased: [CaptureJSON] = []
        for item in (user.purchasedItems ?? []).prefix(40) {
            purchased.append(.object([
                "product_id": .string(item.productID),
                "type": .string(item.productType),
                "expiration": .number(item.expirationDate.timeIntervalSince1970)
            ]))
        }
        let planDetails = user.planDetails
        let tvePlanIds = user.tveSubscrptionPlanIdList ?? []
        let rootPurchased = VLRootViewModel.rootViewModel.purchasedContentIds
        return [
            "is_subscribed": .bool(user.isSubscribed ?? false),
            "is_user_subscribed_effective": .bool(user.isUserSubscribed),
            "ud_is_subscribed": .bool(udBool(.isSubscribed)),
            "plan_id": .string(user.planId ?? ""),
            "plan_identifier": .string(user.planIdentifier ?? ""),
            "plan_product_id": .string(user.planProductId ?? ""),
            "subscription_plan": .string(user.subscriptionPlan ?? ""),
            "subscription_status": .string(user.subscriptionStatus ?? ""),
            "subscription_end_date": .string(user.subscriptionEndDate ?? ""),
            "payment_processor": .string(user.paymentProcessor ?? ""),
            "payment_method": .string(user.paymentMethod ?? ""),
            "payment_handler": .string(user.paymentHandler ?? ""),
            "renewal_cycle_type": .string(user.renewalCycleType ?? ""),
            "is_renewable_available": .bool(user.isRenewableAvailable ?? false),
            "billing_history_available": .bool(user.isBillingHistoryAvailable ?? false),
            "plan_price": .number(planDetails.planPrice ?? 0),
            "plan_currency": .string(planDetails.currencyCode ?? ""),
            "plan_price_string": .string(planDetails.planPriceString ?? ""),
            "purchased_items": .array(purchased),
            "tve_subscription_plan_ids": .array(tvePlanIds.prefix(40).map(CaptureJSON.string)),
            "purchased_content_ids_count": .number(Double(rootPurchased.count)),
            "purchased_content_ids_sample": .array(rootPurchased.prefix(20).map(CaptureJSON.string)),
            "account_linked": .bool(udBool(.isAccountLinked)),
            "allow_both_svod_tve": .bool(AppConfiguration.shared.allowBothSvodTveActivation)
        ]
    }

    // MARK: - TVE

    private func tveSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        let settings = AppConfiguration.shared.tveSettings
        let selected = AdobePrimeTimeServiceV2.shared.getSelectedProviderTitle()
        return [
            "signed_in_via_tve": .bool(user?.isUserLoggedInUsingTVE() ?? false),
            "ud_logged_in_with_tve": .bool(udBool(.isUserLoggedInWithTVE)),
            "tve_user_id": .string(user?.tveUserId ?? udString(.tveUserId)),
            "mvpd_provider": .string(user?.mvpdProvider ?? udString(.mvpdProvider)),
            "tve_provider_user": .string(user?.tveProvider ?? ""),
            "selected_provider_title": .string(selected ?? ""),
            "idp_name": .string(user?.idpName ?? ""),
            "idp_logo_present": .bool(user?.checkIdpLogoExists() ?? false),
            "requestor_id": .string(settings?.requestorId ?? ""),
            "tve_provider_type": .string(settings?.tveProvider?.rawValue ?? ""),
            "authn_enabled": .bool(settings?.isAuthenticationEnabled ?? false),
            "authz_enabled": .bool(settings?.isAuthorizationEnabled ?? false),
            "temp_pass_enabled": .bool(settings?.tempPassEnabled ?? false),
            "provider_count": .number(Double(settings?.providerList?.count ?? 0)),
            "only_tve_flow": .bool(AppConfiguration.shared.onlyTVEFlow),
            "household_id_present": .bool(!udString(.householdID).isEmpty),
            "hba_status": .string(udString(.hbaStatus)),
            "tve_logo_url_present": .bool(!udString(.tveLogoUrl).isEmpty)
        ]
    }

    // MARK: - Profiles

    private func profilesSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        let profiles = user?.profiles ?? []
        let rows: [CaptureJSON] = profiles.prefix(20).map { profile in
            .object([
                "profile_id": .string(profile.profileId ?? ""),
                "is_default": .string(profile.isDefault ?? ""),
                "content_filter_id": .string(profile.contentFilterId ?? "")
            ])
        }
        return [
            "count": .number(Double(profiles.count)),
            "default_profile_id": .string(user?.defaultProfile?.profileId ?? ""),
            "profiles": .array(rows)
        ]
    }

    // MARK: - Personalization

    private func personalizationSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        return [
            "is_personalized": .bool(user?.isPersonalized ?? false),
            "teams_count": .number(Double(user?.teams?.count ?? 0)),
            "categories_count": .number(Double(user?.categories?.count ?? 0)),
            "persons_count": .number(Double(user?.persons?.count ?? 0)),
            "leagues_count": .number(Double(user?.leagues?.count ?? 0))
        ]
    }

    // MARK: - Player prefs

    private func playerPrefsSection() -> [String: CaptureJSON] {
        [
            "autoplay": .bool(udBool(.autoPlay)),
            "cc_enabled": .bool(udBool(.isCCEnabled)),
            "cc_from_settings": .bool(udBool(.ccEnabledFromSettings)),
            "playback_quality": .string(udString(.playbackQuality)),
            "audio_language": .string(udString(.audioLanguage)),
            "audio_track": .string(udString(.audioTrack)),
            "mini_player_disabled": .bool(udBool(.isMiniPlayerDisabled)),
            "live_video_toggle": .bool(udBool(.liveVideoToggle)),
            "player_graphics_visible": .bool(udBool(.playerGraphicsVisible)),
            "total_preview_viewed": jsonFromAny(
                UserDefaultsManager.shared.getSynchronously(forKey: .totalPreviewViewed) as Any,
                depth: 0
            )
        ]
    }

    // MARK: - Content filter

    private func contentFilterSection() -> [String: CaptureJSON] {
        let filter = AppConfiguration.shared.contentFilterObject
        let selected = filter?.selectedFilterItem
        let items = (filter?.contentFilterItems ?? []).prefix(30).map { item -> CaptureJSON in
            .object([
                "id": .string(item.contentFilterId ?? ""),
                "name": .string(item.contentFilterName ?? "")
            ])
        }
        return [
            "configured": .bool(filter != nil),
            "require_selection": .bool(filter?.requireFilterSelection ?? false),
            "hide_from_nav": .bool(filter?.hideFilterOptionFromNav ?? false),
            "selected_id": .string(selected?.contentFilterId ?? ""),
            "selected_name": .string(selected?.contentFilterName ?? ""),
            "item_count": .number(Double(filter?.contentFilterItems?.count ?? 0)),
            "items": .array(Array(items))
        ]
    }

    // MARK: - Theme

    private func themeSection() -> [String: CaptureJSON] {
        let config = AppConfiguration.shared
        var out: [String: CaptureJSON] = [
            "background_color": .string(config.backgroundColor ?? ""),
            "block_title_color": .string(config.appBlockTitleColor ?? ""),
            "page_title_color": .string(config.pageTitleColor ?? ""),
            "link_color": .string(config.linkColor ?? ""),
            "font_family": .string(config.appFontFamily ?? ""),
            "available_fonts": .array(
                config.availableFonts.prefix(40).map { font in
                    CaptureJSON.object([
                        "name": .string(font.name),
                        "url": .string(font.url),
                        "weights": .array(font.fontWeights.map(CaptureJSON.string))
                    ])
                }
            )
        ]
        #if os(iOS)
        // DownloadManagerUserDefaults is iOS-target only.
        out["primary_cta_bg"] = .string(DownloadManagerUserDefaults.Theme.primaryCTABackgroundColor)
        out["card_bg"] = .string(DownloadManagerUserDefaults.Theme.cardBackgroundColor)
        out["app_text_color"] = .string(DownloadManagerUserDefaults.Theme.appTextColor)
        #endif
        return out
    }

    // MARK: - Config

    private func configSection() -> [String: CaptureJSON] {
        let config = AppConfiguration.shared
        return [
            "site_id": .string(config.siteId ?? ""),
            "site_name": .string(config.sitename ?? ""),
            "api_base_url": .string(config.apiBaseUrl ?? ""),
            "domain_name": .string(config.domainName ?? udString(.domainName)),
            "environment": .string(VLSiteConfiguration.shared.config?.env ?? udString(.environment)),
            "only_tve_flow": .bool(config.onlyTVEFlow),
            "parental_control_feature": .bool(config.isParentalControlEnabled),
            "app_has_tab_bar": .bool(config.appHasTabBar),
            "coppa_compliant": .bool(VLSiteConfiguration.shared.config?.isCoppaCompliant ?? false)
        ]
    }

    // MARK: - Navigation

    private func navigationSection() -> [String: CaptureJSON] {
        let top = Utility.sharedUtility.topViewController()
        return [
            "top_vc": .string(top.map { String(describing: type(of: $0)) } ?? ""),
            "ud_page_id": .string(udString(.pageID)),
            "ud_page_path": .string(udString(.pagePath)),
            "onboarding_flow": .string(udString(.onboardingFlow)),
            "page_vc_count": .number(Double(AppConfiguration.shared.pageViewControllers.count))
        ]
    }

    // MARK: - Page / modules

    private func pageSection() -> [String: CaptureJSON] {
        #if os(iOS)
        return pageSectionIOS()
        #else
        return pageSectionTvOS()
        #endif
    }

    #if os(iOS)
    private func pageSectionIOS() -> [String: CaptureJSON] {
        guard let pageVC = Utility.sharedUtility.topViewController() as? VLPageViewController,
              let viewModel = pageVC.viewModel else {
            let top = Utility.sharedUtility.topViewController()
            return [
                "visible": .bool(false),
                "platform": .string("iOS"),
                "top_vc": .string(top.map { String(describing: type(of: $0)) } ?? ""),
                "note": .string("Top VC is not VLPageViewController (guest onboarding / player / modal ok)")
            ]
        }
        let page = viewModel.page
        let modules = viewModel.modules
        let api = viewModel.pageAPiObject
        return [
            "visible": .bool(true),
            "platform": .string("iOS"),
            "source": .string("VLPageViewController"),
            "page_id": .string(page.pageId ?? api?.pageId ?? ""),
            "page_path": .string(page.pagePath ?? api?.pagePath ?? ""),
            "page_title": .string(page.pageTitle ?? api?.pageTitle ?? ""),
            "module_count": CaptureJSON.number(Double(modules.count)),
            "modules": .array(
                modules.prefix(80).map { CaptureJSON.object(moduleDump($0)) }
            )
        ]
    }
    #endif

    #if os(tvOS)
    private func pageSectionTvOS() -> [String: CaptureJSON] {
        let top = Utility.sharedUtility.topViewController()
        var modules: [SFModuleObject] = []
        var pageId = ""
        var pagePath = ""
        var pageTitle = ""
        var source = "none"

        if let container = findModuleContainer(from: top) {
            modules = container.modulesListArray
            applyPageAPI(container.pageAPIObject, modules: &modules, pageId: &pageId, pagePath: &pagePath, pageTitle: &pageTitle)
            source = "ModuleContainerViewController_tvOS"
        } else {
            for item in AppConfiguration.shared.pageViewControllers {
                guard let container = item as? ModuleContainerViewController_tvOS else { continue }
                modules = container.modulesListArray
                applyPageAPI(container.pageAPIObject, modules: &modules, pageId: &pageId, pagePath: &pagePath, pageTitle: &pageTitle)
                source = "pageViewControllers"
                break
            }
        }

        var extras: [CaptureJSON] = []
        if let plans = Constants.shared.kAPPDELEGATE.contentPlansModuleObject {
            extras.append(.object(moduleDump(plans)))
        }

        let visible = !modules.isEmpty || !pageId.isEmpty || !extras.isEmpty
        return [
            "visible": .bool(visible),
            "platform": .string("tvOS"),
            "source": .string(source),
            "top_vc": .string(top.map { String(describing: type(of: $0)) } ?? ""),
            "page_id": .string(pageId),
            "page_path": .string(pagePath),
            "page_title": .string(pageTitle),
            "module_count": CaptureJSON.number(Double(modules.count)),
            "modules": .array(modules.prefix(80).map { CaptureJSON.object(moduleDump($0)) }),
            "content_plans_module": .array(extras),
            "note": .string(
                visible
                    ? ""
                    : "No ModuleContainerViewController_tvOS with modules yet (boot / player / modal ok)"
            )
        ]
    }

    private func applyPageAPI(
        _ api: PageAPIObject?,
        modules: inout [SFModuleObject],
        pageId: inout String,
        pagePath: inout String,
        pageTitle: inout String
    ) {
        guard let api else { return }
        pageId = api.pageId ?? pageId
        pagePath = api.pagePath ?? pagePath
        pageTitle = api.pageTitle ?? pageTitle
        if modules.isEmpty {
            modules = api.modules
        }
        if modules.isEmpty, let dict = api.pageModules {
            modules = dict.values.compactMap { $0 as? SFModuleObject }
        }
    }

    private func findModuleContainer(from vc: UIViewController?) -> ModuleContainerViewController_tvOS? {
        guard let vc else { return nil }
        if let container = vc as? ModuleContainerViewController_tvOS {
            return container
        }
        if let nav = vc as? UINavigationController {
            return findModuleContainer(from: nav.visibleViewController ?? nav.topViewController)
        }
        if let tab = vc as? UITabBarController {
            return findModuleContainer(from: tab.selectedViewController)
        }
        for child in vc.children {
            if let found = findModuleContainer(from: child) {
                return found
            }
        }
        if let presented = vc.presentedViewController {
            return findModuleContainer(from: presented)
        }
        return nil
    }
    #endif

    private func moduleDump(_ module: SFModuleObject) -> [String: CaptureJSON] {
        var layout: [String: CaptureJSON] = [:]
        if let moduleLayout = module.moduleLayout {
            layout["id"] = .string(moduleLayout.id ?? "")
            layout["type"] = .string(moduleLayout.type ?? "")
            layout["view"] = .string(moduleLayout.view ?? "")
            layout["block_name"] = .string(moduleLayout.blockName ?? "")
            layout["module_type"] = .string(moduleLayout.moduleType ?? "")
            if let settings = moduleLayout.settings {
                layout["settings"] = .object(mirrorObject(settings, depth: 0))
            }
            layout["font_settings"] = .object(
                mirrorObject(moduleLayout.resolvedFontSettings(), depth: 0)
            )
        }
        var metadata: [String: CaptureJSON] = [:]
        for (key, value) in (module.metadataMap ?? [:]).sorted(by: { $0.key < $1.key }).prefix(80) {
            metadata[key] = .string(value)
        }
        return [
            "module_id": .string(module.moduleId ?? ""),
            "module_type": .string(module.moduleType ?? ""),
            "module_title": .string(module.moduleTitle ?? ""),
            "module_subtitle": .string(module.moduleSubTitle ?? ""),
            "content_type": .string(module.contentType ?? ""),
            "display_limit": .number(Double(module.moduleDisplayLimit ?? 0)),
            "item_count": .number(Double(module.moduleData?.count ?? 0)),
            "metadata_map": .object(metadata),
            "layout": .object(layout)
        ]
    }

    // MARK: - Analytics

    private func analyticsSection() -> [String: CaptureJSON] {
        let config = AppConfiguration.shared
        let beacon = config.beaconObject
        var clients: [String] = []
        if config.doesAppSupportCleverTap { clients.append("cleverTap") }
        if config.doesAppSupportAppsflyer { clients.append("appsFlyer") }
        if config.doesAppSupportBraze { clients.append("braze") }
        if config.analyticsAdobeConfig?.enabled == true { clients.append("adobe") }
        if config.analyticsComscoreConfig?.enable == true { clients.append("comscore") }
        if config.analyticsConvivaConfig?.enabled == true { clients.append("conviva") }
        if config.analyticsDataDogConfig?.enabled == true { clients.append("dataDog") }
        #if os(iOS)
        if config.facebookID != nil { clients.append("facebook") }
        #endif
        return [
            "configured": .bool(VLAnalyticsViewModel.shared.isAnalyticsConfigure),
            "registered_clients": .array(clients.map(CaptureJSON.string)),
            "beacons": .object([
                "user_beacon_url": .string(beacon?.userBeaconUrl ?? ""),
                "player_beacon_url": .string(beacon?.playerBeaconUrl ?? "")
            ]),
            "flags": .object({
                var flags: [String: CaptureJSON] = [
                    "clever_tap": .bool(config.doesAppSupportCleverTap),
                    "apps_flyer": .bool(config.doesAppSupportAppsflyer),
                    "braze": .bool(config.doesAppSupportBraze),
                    "adobe": .bool(config.analyticsAdobeConfig?.enabled == true),
                    "comscore": .bool(config.analyticsComscoreConfig?.enable == true),
                    "conviva": .bool(config.analyticsConvivaConfig?.enabled == true),
                    "data_dog": .bool(config.analyticsDataDogConfig?.enabled == true)
                ]
                #if os(iOS)
                flags["facebook"] = .bool(config.facebookID != nil)
                #else
                flags["facebook"] = .bool(false)
                #endif
                return flags
            }())
        ]
    }

    // MARK: - Device

    private func deviceSection() -> [String: CaptureJSON] {
        let info = Bundle.main.infoDictionary ?? [:]
        #if os(tvOS)
        let platform = "tvOS"
        #else
        let platform = "iOS"
        #endif
        return [
            "platform": .string(platform),
            "os_version": .string(UIDevice.current.systemVersion),
            "model": .string(UIDevice.current.model),
            "bundle_id": .string(Bundle.main.bundleIdentifier ?? ""),
            "app_version": .string(info["CFBundleShortVersionString"] as? String ?? ""),
            "build": .string(info["CFBundleVersion"] as? String ?? ""),
            "device_token_present": .bool(!udString(.deviceToken).isEmpty)
        ]
    }

    // MARK: - Entitlements v2 (UserSessionStore)

    private func entitlementsV2Section() -> [String: CaptureJSON] {
        let pair = readSessionEntitlements()
        let entitlements = pair.entitlements
        let refreshed = pair.lastRefreshedAt
        let purchased = Array(entitlements.purchasedContentIds).sorted()
        return [
            "svod_plan_id": .string(entitlements.svodPlanId ?? ""),
            "is_subscribed": .bool(entitlements.isSubscribed),
            "has_svod": .bool(entitlements.hasSVOD),
            "tve_plan_ids": .array(entitlements.tvePlanIds.prefix(40).map(CaptureJSON.string)),
            "purchased_content_count": .number(Double(purchased.count)),
            "purchased_content_sample": .array(purchased.prefix(20).map(CaptureJSON.string)),
            "is_vip": .bool(entitlements.isVip),
            "plan_product_id": .string(entitlements.planProductId ?? ""),
            "auth_modes": .array(entitlements.authModes.map { CaptureJSON.string($0.rawValue) }),
            "is_device_supported": .bool(entitlements.isDeviceSupportedOnCurrentPlatform),
            "is_globally_entitled": .bool(entitlements.isGloballyEntitled),
            "profile_id": .string(entitlements.profileId ?? ""),
            "user_state": .string(entitlements.userState ?? ""),
            "last_refreshed_at": .number(refreshed?.timeIntervalSince1970 ?? 0),
            "root_purchased_count": .number(Double(VLRootViewModel.rootViewModel.purchasedContentIds.count))
        ]
    }

    private func readSessionEntitlements() -> (entitlements: VLUserEntitlements, lastRefreshedAt: Date?) {
        let work: @MainActor () -> (VLUserEntitlements, Date?) = {
            (
                UserSessionStoreObservable.shared.entitlements,
                UserSessionStoreObservable.shared.lastRefreshedAt
            )
        }
        if Thread.isMainThread {
            return MainActor.assumeIsolated(work)
        }
        return DispatchQueue.main.sync {
            MainActor.assumeIsolated(work)
        }
    }

    // MARK: - Entitlement API cache

    private func entitlementCacheSection() -> [String: CaptureJSON] {
        let bulk = VLQuadPlayerEntitilement.shared
        let cache = bulk.entitlementData ?? [:]
        let keys = Array(cache.keys).sorted()
        var sampleRows: [CaptureJSON] = []
        for key in keys.prefix(12) {
            guard let row = cache[key] else { continue }
            let success = row.isSuccess ?? false
            sampleRows.append(.object([
                "content_id": .string(key),
                "is_success": .bool(success),
                "playable": .bool(row.error?.isPlayable ?? success),
                "error_code": .string(row.error?.errorCode ?? ""),
                "vl_error_code": .string(row.error?.vl_errorCode ?? "")
            ]))
        }
        return [
            "bulk_key_count": .number(Double(keys.count)),
            "bulk_keys_sample": .array(keys.prefix(20).map(CaptureJSON.string)),
            "bulk_requested_keys": .array((bulk.bulkEntitlementKeys ?? []).prefix(20).map(CaptureJSON.string)),
            "was_logged_in": jsonFromAny(bulk.wasUserLoggedIN as Any, depth: 0),
            "was_subscribed": jsonFromAny(bulk.wasUserSubscribed as Any, depth: 0),
            "sample": .array(sampleRows),
            "endpoint_hint": .string(
                AppConfiguration.shared.entitlementBaseUrl != nil
                    ? "entitlementBaseUrl/videos"
                    : "/v3/entitlement/videos"
            )
        ]
    }

    // MARK: - Plan feature gates

    private func planFeaturesSection() -> [String: CaptureJSON] {
        let user = ParentalControlHelper.getUserDetails()
        let setting = user?.featureSetting
        let iosFeatures = (setting?[.ios] ?? []).map(\.rawValue)
        let tvFeatures = (setting?[.appleTV] ?? []).map(\.rawValue)
        return [
            "ios": .array(iosFeatures.map(CaptureJSON.string)),
            "apple_tv": .array(tvFeatures.map(CaptureJSON.string)),
            "has_feature_setting": .bool(setting != nil)
        ]
    }

    // MARK: - Devices

    private func devicesSection() -> [String: CaptureJSON] {
        #if os(iOS)
        let devices = ParentalControlHelper.getUserDetails()?.listOfDevices ?? []
        let items: [CaptureJSON] = devices.prefix(30).map { device in
            .object([
                "device_id": .string(device.deviceId ?? ""),
                "name": .string(device.deviceName ?? "")
            ])
        }
        return [
            "count": .number(Double(devices.count)),
            "items": .array(items),
            "source": .string(devices.isEmpty ? "none" : "sf_user"),
            "platform_note": .string("listOfDevices is iOS memory only")
        ]
        #else
        return [
            "count": .number(0),
            "items": .array([]),
            "source": .string("none"),
            "platform_note": .string("UserDevice list not compiled on tvOS")
        ]
        #endif
    }

    // MARK: - Watchlist / history

    private func userListsSection() -> [String: CaptureJSON] {
        let manager = VLRootViewModel.rootViewModel.userContentManager
        let watchlist = gridIds(from: manager.watchList)
        let history = gridIds(from: manager.historyList)
        return [
            "watchlist_count": .number(Double(watchlist.count)),
            "history_count": .number(Double(history.count)),
            "watchlist_sample_ids": .array(watchlist.prefix(20).map(CaptureJSON.string)),
            "history_sample_ids": .array(history.prefix(20).map(CaptureJSON.string)),
            "watchlist_loaded": .bool(manager.watchList != nil),
            "history_loaded": .bool(manager.historyList != nil)
        ]
    }

    private func gridIds(from list: Any?) -> [String] {
        if let grids = list as? [SFGridObject] {
            return grids.compactMap(\.contentId)
        }
        return []
    }

    // MARK: - Live player / video

    private func playerSection() -> [String: CaptureJSON] {
        #if os(tvOS)
        let platform = "tvOS"
        #else
        let platform = "iOS"
        #endif
        let top = Utility.sharedUtility.topViewController()
        guard let player = findPlayerController(from: top) else {
            return [
                "visible": .bool(false),
                "platform": .string(platform),
                "top_vc": .string(top.map { String(describing: type(of: $0)) } ?? ""),
                "note": .string("No VLPlayerMFEController in hierarchy")
            ]
        }
        let grid = player.viewModel.data
        #if os(iOS)
        let duration = player.totalTime ?? grid.totalTime ?? 0
        #else
        let duration = grid.totalTime ?? 0
        #endif
        let playhead = player.videoCurrentPlayhead
        let watchedPct: Double
        if duration > 0 {
            watchedPct = min(100, max(0, (playhead / duration) * 100))
        } else {
            watchedPct = 0
        }
        let monetization = (grid.monetizationModels ?? []).map { String(describing: $0) }
        let planIds = grid.plans.compactMap(\.id)
        let planNames = grid.plans.compactMap(\.name)
        let responseDict = player.contentResponse
            ?? (player.entitlementData?.contentResponse as? [String: AnyObject])
        let stream = streamMeta(from: responseDict)
        return [
            "visible": .bool(true),
            "platform": .string(platform),
            "vc": .string(String(describing: type(of: player))),
            "content_id": .string(grid.contentId ?? ""),
            "playback_id": .string(player.currentPlaybackId ?? ""),
            "title": .string(grid.contentTitle ?? ""),
            "content_type": .string(grid.contentType ?? ""),
            "is_live": .bool(grid.isLiveStream ?? false),
            "is_drm": .bool(grid.isDRMEnabled ?? false),
            "monetization_models": .array(monetization.map(CaptureJSON.string)),
            "monetization_badge": .string(grid.monetizationBadgeIdentifier?.rawValue ?? ""),
            "plan_ids": .array(planIds.prefix(20).map(CaptureJSON.string)),
            "plan_names": .array(planNames.prefix(20).map(CaptureJSON.string)),
            "is_free_preview": .bool(player.isFreePreview),
            "is_paused": .bool(player.isVideoPaused),
            "playhead_sec": .number(playhead),
            "duration_sec": .number(duration),
            "watched_pct": .number(watchedPct),
            "ssai": .bool(grid.isSSAIEnabled),
            "dvr": .bool(grid.isDvrEnabled ?? false),
            "stream": .object(stream),
            "entitlement": .object(entitlementDump(player.entitlementData))
        ]
    }

    private func findPlayerController(from vc: UIViewController?) -> VLPlayerMFEController? {
        guard let vc else { return nil }
        if let player = vc as? VLPlayerMFEController {
            return player
        }
        if let nav = vc as? UINavigationController {
            return findPlayerController(from: nav.visibleViewController ?? nav.topViewController)
        }
        if let tab = vc as? UITabBarController {
            return findPlayerController(from: tab.selectedViewController)
        }
        for child in vc.children {
            if let found = findPlayerController(from: child) {
                return found
            }
        }
        if let presented = vc.presentedViewController {
            return findPlayerController(from: presented)
        }
        return nil
    }

    private func entitlementDump(_ data: VLPlayer.EntitlementData?) -> [String: CaptureJSON] {
        guard let data else {
            return ["present": .bool(false)]
        }
        let success = data.isSuccess ?? false
        return [
            "present": .bool(true),
            "is_success": .bool(success),
            "playable": .bool(data.error?.isPlayable ?? success),
            "error_code": .string(data.error?.errorCode ?? ""),
            "vl_error_code": .string(data.error?.vl_errorCode ?? ""),
            "error_message": .string(data.error?.errorMessage ?? ""),
            "has_player_object": .bool(data.playerObject != nil),
            "has_player_response": .bool(data.playerResponse != nil),
            "has_content_response": .bool(data.contentResponse != nil)
        ]
    }

    private func streamMeta(from contentResponse: [String: AnyObject]?) -> [String: CaptureJSON] {
        guard let contentResponse else {
            return [
                "hls_present": .bool(false),
                "fairplay_present": .bool(false),
                "widevine_present": .bool(false),
                "mpeg_present": .bool(false),
                "is_4k": .bool(false),
                "source_host": .string("")
            ]
        }
        let video = contentResponse["video"] as? [String: Any] ?? contentResponse as [String: Any]
        let streaming = video["streamingInfo"] as? [String: Any]
        let assets = streaming?["videoAssets"] as? [String: Any] ?? [:]
        var host = ""
        if let hls = assets["hls"] as? String, let url = URL(string: hls) {
            host = url.host ?? ""
        } else if let fairPlay = assets["fairPlay"] as? [String: Any] {
            let urlString = (fairPlay["url"] as? String) ?? (fairPlay["streamUrl"] as? String)
            if let urlString, let url = URL(string: urlString) {
                host = url.host ?? ""
            }
        }
        return [
            "hls_present": .bool(assets["hls"] != nil),
            "fairplay_present": .bool(assets["fairPlay"] != nil),
            "widevine_present": .bool(assets["widevine"] != nil),
            "mpeg_present": .bool(assets["mpeg"] != nil),
            "is_4k": .bool((assets["is4K"] as? Bool) ?? false),
            "source_host": .string(host)
        ]
    }

    // MARK: - Site features / DRM / SSO / nav catalog

    private func siteFeaturesSection() -> [String: CaptureJSON] {
        let config = AppConfiguration.shared
        let serviceTypes = config.serviceTypes.map { serviceTypeLabel($0) }
        return [
            "login_module": .bool(config.login_module_enabled),
            "signup_module": .bool(config.signup_module_enabled),
            "watchlist_module": .bool(config.watchlist_module_enabled),
            "web_subscription_only": .bool(config.isWebSubscription ?? false),
            "new_onboarding": .bool(config.isNewOnboardingEnabled),
            "video_preview_app": .bool(config.isVideoPreviewEnabledForApp ?? false),
            "video_preview_per_video": .bool(config.isVideoPreviewEnabledPerVideo ?? false),
            "video_preview_recurring": .bool(config.isVideoPreviewRecurring ?? false),
            "video_preview_duration": .string(config.videoPreviewDuration ?? ""),
            "service_types": .array(serviceTypes.map(CaptureJSON.string)),
            "entitlement_base_url_present": .bool(!(config.entitlementBaseUrl ?? "").isEmpty),
            "api_base_url_present": .bool(!(config.apiBaseUrl ?? "").isEmpty),
            "only_tve_flow": .bool(config.onlyTVEFlow),
            "allow_both_svod_tve": .bool(config.allowBothSvodTveActivation),
            "app_has_tab_bar": .bool(config.appHasTabBar),
            "parental_control_feature": .bool(config.isParentalControlEnabled),
            "holes_count": .number(Double(config.holes?.count ?? 0))
        ]
    }

    private func serviceTypeLabel(_ type: ServiceType) -> String {
        switch type {
        case .SVOD: return "SVOD"
        case .AVOD: return "AVOD"
        case .TVOD: return "TVOD"
        case .TVE(let provider):
            switch provider {
            case .some(.AdobePass): return "TVE:AdobePass"
            case .some(.Verimatrix): return "TVE:Verimatrix"
            case .none: return "TVE"
            }
        }
    }

    private func drmSection() -> [String: CaptureJSON] {
        let drm = AppConfiguration.shared.drmDetails
        return [
            "url_scheme": .string(drm?.urlScheme ?? ""),
            "message_present": .bool(!(drm?.drmMessage ?? "").isEmpty),
            "configured": .bool(drm != nil)
        ]
    }

    private func ssoSection() -> [String: CaptureJSON] {
        let caliente = AppConfiguration.shared.ssoConfig?.calienteMx
        return [
            "caliente_mx_enabled": .bool(caliente?.isEnabled == true),
            "app_id_present": .bool(!(caliente?.appId ?? "").isEmpty),
            "secret_exposed": .bool(false)
        ]
    }

    private func navigationCatalogSection() -> [String: CaptureJSON] {
        let dict = AppConfiguration.shared.navigationMenu.navigationItemDict
        func items(_ key: String) -> [CaptureJSON] {
            (dict[key] ?? []).prefix(30).map { item in
                CaptureJSON.object([
                    "title": .string(item.title ?? ""),
                    "page_url": .string(item.pageUrl ?? ""),
                    "page_id": .string(item.pageId),
                    "logged_in": .bool(item.loggedIn),
                    "logged_out": .bool(item.loggedOut),
                    "subscribed": .bool(item.subscribed)
                ])
            }
        }
        let onboardingPaths = (dict["onboarding"] ?? []).compactMap(\.pageUrl)
        return [
            "tab_bar": .array(items("tabBar")),
            "primary": .array(items("primary")),
            "footer_count": .number(Double((dict["footer"] ?? []).count)),
            "user_count": .number(Double((dict["user"] ?? []).count)),
            "onboarding_paths": .array(onboardingPaths.prefix(20).map(CaptureJSON.string)),
            "primary_count": .number(Double((dict["primary"] ?? []).count)),
            "tab_bar_count": .number(Double((dict["tabBar"] ?? []).count))
        ]
    }

    // MARK: - Helpers

    private func udString(_ key: UserDefaultsManager.Keys) -> String {
        (UserDefaultsManager.shared.getSynchronously(forKey: key) as? String) ?? ""
    }

    private func udBool(_ key: UserDefaultsManager.Keys) -> Bool {
        if let value = UserDefaultsManager.shared.getSynchronously(forKey: key) as? Bool {
            return value
        }
        if let number = UserDefaultsManager.shared.getSynchronously(forKey: key) as? NSNumber {
            return number.boolValue
        }
        return false
    }

    private func mirrorObject(_ value: Any, depth: Int) -> [String: CaptureJSON] {
        guard depth < 3 else { return ["truncated": .bool(true)] }
        let mirror = Mirror(reflecting: value)
        var out: [String: CaptureJSON] = [:]
        for child in mirror.children.prefix(80) {
            guard let label = child.label else { continue }
            out[label] = jsonFromAny(child.value, depth: depth + 1)
        }
        return out
    }

    private func jsonFromAny(_ value: Any, depth: Int) -> CaptureJSON {
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .optional {
            guard let first = mirror.children.first else { return .null }
            return jsonFromAny(first.value, depth: depth)
        }
        switch value {
        case let item as String: return .string(item)
        case let item as Bool: return .bool(item)
        case let item as Int: return .number(Double(item))
        case let item as Double: return .number(item)
        case let item as Float: return .number(Double(item))
        case let item as Date: return .number(item.timeIntervalSince1970)
        case let item as NSNumber: return .number(item.doubleValue)
        case let item as [String: Any]:
            return .object(item.mapValues { jsonFromAny($0, depth: depth + 1) })
        case let item as [Any]:
            return .array(item.prefix(40).map { jsonFromAny($0, depth: depth + 1) })
        default:
            if mirror.children.isEmpty {
                return .string(String(describing: value))
            }
            if depth >= 3 {
                return .string(String(describing: value))
            }
            return .object(mirrorObject(value, depth: depth))
        }
    }

    private func redactEmail(_ email: String?) -> String {
        guard let email, email.contains("@") else { return email ?? "" }
        let parts = email.split(separator: "@", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return "***" }
        let name = parts[0]
        let prefix = name.prefix(1)
        return "\(prefix)***@\(parts[1])"
    }

}
#endif
