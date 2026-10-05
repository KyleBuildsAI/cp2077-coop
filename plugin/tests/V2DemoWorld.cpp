#include "V2DemoWorld.hpp"

#include "v2/SnapshotBuffer.hpp"
#include "v2/coop_proto_v2.h"

#include <algorithm>
#include <cmath>
#include <numbers>

namespace coopnet::v2::demo
{
namespace
{
// CPython's math.degrees / math.radians constants.
constexpr double kRadToDeg = 180.0 / std::numbers::pi;
constexpr double kDegToRad = std::numbers::pi / 180.0;

std::array<double, 4> YawQuat(double aYawDegrees)
{
    const double half = aYawDegrees * kDegToRad / 2.0;
    return {0.0, 0.0, std::sin(half), std::cos(half)};
}

// ---- the scripted course (testworld.py COURSE_HALF) ---------------------------------------------

constexpr double kWalkMps = 1.6;
constexpr double kRunMps = 4.5;
constexpr double kSprintMps = 7.5;

enum class SegmentKind : uint8_t
{
    Line, // a = length m, b = start speed, c = end speed (constant acceleration)
    Arc,  // a = turn degrees (+ = left), b = radius m, c = speed
    Wait, // a = seconds
};

struct CourseStep
{
    const char* motion;
    SegmentKind kind;
    double a;
    double b;
    double c;
};

constexpr CourseStep kCourseHalf[] = {
    {"walk", SegmentKind::Line, 6.0, kWalkMps, kWalkMps},
    {"turn", SegmentKind::Arc, 90.0, 1.5, kWalkMps},
    {"run", SegmentKind::Line, 2.0, kWalkMps, kRunMps},
    {"run", SegmentKind::Line, 14.0, kRunMps, kRunMps},
    {"turn", SegmentKind::Arc, -60.0, 1.5, kRunMps},
    {"turn", SegmentKind::Line, 3.0, kRunMps, kRunMps},
    {"turn", SegmentKind::Arc, 120.0, 1.5, kRunMps},
    {"turn", SegmentKind::Line, 3.0, kRunMps, kRunMps},
    {"turn", SegmentKind::Arc, -60.0, 1.5, kRunMps},
    {"sprint", SegmentKind::Line, 3.0, kRunMps, kSprintMps},
    {"sprint", SegmentKind::Line, 24.0, kSprintMps, kSprintMps},
    {"turn", SegmentKind::Arc, 90.0, 5.0, kSprintMps},
    {"stop", SegmentKind::Line, 4.0, kSprintMps, 0.0},
    {"stop", SegmentKind::Wait, 1.0, 0.0, 0.0},
    {"walk", SegmentKind::Line, 1.0, 0.0, kWalkMps},
};

struct CourseSegment
{
    const CourseStep* step = nullptr;
    double t0 = 0.0;
    double duration = 0.0;
    double x = 0.0;
    double y = 0.0;
    double heading = 0.0; // radians, 0 = +X, counter-clockwise
};

struct Course
{
    std::vector<CourseSegment> segments;
    double period = 0.0;
    double phase = 0.0;
};

Course BuildCourse(double aStartX, double aStartY, double aHeadingDeg, double aPhaseS)
{
    Course course;
    course.phase = aPhaseS;
    double t = 0.0;
    double x = aStartX;
    double y = aStartY;
    double heading = aHeadingDeg * kDegToRad;
    for (int half = 0; half < 2; ++half)
    {
        for (const CourseStep& step : kCourseHalf)
        {
            CourseSegment segment{&step, t, 0.0, x, y, heading};
            if (step.kind == SegmentKind::Line)
            {
                segment.duration = 2.0 * step.a / (step.b + step.c);
                x += step.a * std::cos(heading);
                y += step.a * std::sin(heading);
            }
            else if (step.kind == SegmentKind::Arc)
            {
                const double sign = step.a > 0 ? 1.0 : -1.0;
                const double turn = std::abs(step.a) * kDegToRad;
                segment.duration = turn * step.b / step.c;
                const double cx = x - sign * step.b * std::sin(heading);
                const double cy = y + sign * step.b * std::cos(heading);
                heading += sign * turn;
                x = cx + sign * step.b * std::sin(heading);
                y = cy - sign * step.b * std::cos(heading);
            }
            else
            {
                segment.duration = step.a;
            }
            course.segments.push_back(segment);
            t += segment.duration;
        }
    }
    course.period = t;
    return course;
}

const Course& CourseFor(uint8_t aRole)
{
    static const Course host = BuildCourse(kOrigin[0] - 10.0, kOrigin[1] - 10.0, 0.0, 0.0);
    static const Course joiner = BuildCourse(kOrigin[0] + 25.0, kOrigin[1] - 5.0, 90.0, 9.0);
    return aRole == static_cast<uint8_t>(coopv2::Role::Host) ? host : joiner;
}

uint8_t CourseMoveState(double aSpeed)
{
    using coopv2::MoveState;
    if (aSpeed < 0.05)
        return static_cast<uint8_t>(MoveState::Idle);
    if (aSpeed < 2.5)
        return static_cast<uint8_t>(MoveState::Walk);
    if (aSpeed < 6.0)
        return static_cast<uint8_t>(MoveState::Run);
    return static_cast<uint8_t>(MoveState::Sprint);
}

PlayerTruth CourseTruth(uint8_t aRole, double aT)
{
    const Course& course = CourseFor(aRole);
    const double local = PyFloatMod(aT + course.phase, course.period);
    const auto after = std::upper_bound(course.segments.begin(), course.segments.end(), local,
                                        [](double aValue, const CourseSegment& aSegment) { return aValue < aSegment.t0; });
    const CourseSegment& segment = after == course.segments.begin() ? course.segments.front() : *(after - 1);
    const CourseStep& step = *segment.step;
    const double tau = std::min(std::max(local - segment.t0, 0.0), segment.duration);
    double x = segment.x;
    double y = segment.y;
    double vx = 0.0;
    double vy = 0.0;
    double heading = segment.heading;
    if (step.kind == SegmentKind::Line)
    {
        const double travelled = step.b * tau + (step.c - step.b) * tau * tau / (2.0 * segment.duration);
        const double speed = step.b + (step.c - step.b) * tau / segment.duration;
        const double cosH = std::cos(heading);
        const double sinH = std::sin(heading);
        x += travelled * cosH;
        y += travelled * sinH;
        vx = speed * cosH;
        vy = speed * sinH;
    }
    else if (step.kind == SegmentKind::Arc)
    {
        const double sign = step.a > 0 ? 1.0 : -1.0;
        const double cx = x - sign * step.b * std::sin(heading);
        const double cy = y + sign * step.b * std::cos(heading);
        heading += sign * step.c * tau / step.b;
        x = cx + sign * step.b * std::sin(heading);
        y = cy - sign * step.b * std::cos(heading);
        vx = step.c * std::cos(heading);
        vy = step.c * std::sin(heading);
    }
    PlayerTruth truth;
    truth.pos = {x, y, kOrigin[2]};
    truth.vel = {vx, vy, 0.0};
    truth.yaw = YawFromForward(std::cos(heading), std::sin(heading));
    truth.quat = YawQuat(truth.yaw);
    truth.driving = false;
    truth.motion = step.motion;
    truth.move = CourseMoveState(std::hypot(vx, vy));
    return truth;
}
} // namespace

// ---- PyRandom ------------------------------------------------------------------------------------

PyRandom::PyRandom(uint64_t aSeed)
{
    std::vector<uint32_t> key;
    for (uint64_t rest = aSeed; rest != 0; rest >>= 32)
    {
        key.push_back(static_cast<uint32_t>(rest & 0xFFFFFFFFu));
    }
    if (key.empty())
    {
        key.push_back(0);
    }
    InitByArray(key);
}

void PyRandom::InitGenrand(uint32_t aSeed)
{
    m_state[0] = aSeed;
    for (size_t index = 1; index < m_state.size(); ++index)
    {
        const uint32_t previous = m_state[index - 1];
        m_state[index] = 1812433253u * (previous ^ (previous >> 30)) + static_cast<uint32_t>(index);
    }
    m_index = m_state.size();
}

void PyRandom::InitByArray(const std::vector<uint32_t>& aKey)
{
    constexpr size_t n = 624;
    InitGenrand(19650218u);
    size_t i = 1;
    size_t j = 0;
    for (size_t k = std::max(n, aKey.size()); k > 0; --k)
    {
        const uint32_t previous = m_state[i - 1];
        m_state[i] = (m_state[i] ^ ((previous ^ (previous >> 30)) * 1664525u)) + aKey[j] + static_cast<uint32_t>(j);
        ++i;
        ++j;
        if (i >= n)
        {
            m_state[0] = m_state[n - 1];
            i = 1;
        }
        if (j >= aKey.size())
        {
            j = 0;
        }
    }
    for (size_t k = n - 1; k > 0; --k)
    {
        const uint32_t previous = m_state[i - 1];
        m_state[i] = (m_state[i] ^ ((previous ^ (previous >> 30)) * 1566083941u)) - static_cast<uint32_t>(i);
        ++i;
        if (i >= n)
        {
            m_state[0] = m_state[n - 1];
            i = 1;
        }
    }
    m_state[0] = 0x80000000u;
    m_index = n;
}

uint32_t PyRandom::NextU32()
{
    constexpr size_t n = 624;
    constexpr size_t m = 397;
    constexpr uint32_t upper = 0x80000000u;
    constexpr uint32_t lower = 0x7FFFFFFFu;
    constexpr uint32_t matrix = 0x9908B0DFu;
    if (m_index >= n)
    {
        for (size_t k = 0; k < n; ++k)
        {
            const uint32_t y = (m_state[k] & upper) | (m_state[(k + 1) % n] & lower);
            m_state[k] = m_state[(k + m) % n] ^ (y >> 1) ^ ((y & 1u) ? matrix : 0u);
        }
        m_index = 0;
    }
    uint32_t y = m_state[m_index++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9D2C5680u;
    y ^= (y << 15) & 0xEFC60000u;
    y ^= (y >> 18);
    return y;
}

double PyRandom::Random()
{
    const uint32_t high = NextU32() >> 5;
    const uint32_t low = NextU32() >> 6;
    return (high * 67108864.0 + low) * (1.0 / 9007199254740992.0);
}

double PyRandom::Uniform(double aLow, double aHigh)
{
    return aLow + (aHigh - aLow) * Random();
}

// ---- entities ------------------------------------------------------------------------------------

std::vector<EntitySpec> BuildEntities(uint64_t aSeed)
{
    using coopv2::EntityKind;
    PyRandom rng(aSeed);
    std::vector<EntitySpec> specs;
    constexpr double kTwoPi = 2.0 * std::numbers::pi;
    const auto add = [&](EntityKind aKind, const Vec3& aCenter, double aRadius, double aSpeed, double aSpawnAt,
                         std::optional<double> aDespawnAt, std::optional<double> aDeathAt, uint8_t aFlags) {
        EntitySpec spec;
        spec.netId = static_cast<uint16_t>(specs.size() + 1);
        spec.kind = static_cast<uint8_t>(aKind);
        spec.center = aCenter;
        spec.radius = aRadius;
        spec.speed = aSpeed;
        spec.phase = rng.Uniform(0.0, kTwoPi);
        spec.spawnAt = aSpawnAt;
        spec.despawnAt = aDespawnAt;
        spec.deathAt = aDeathAt;
        spec.flags = aFlags;
        specs.push_back(spec);
    };
    // Python evaluates the arguments left to right; the phase is drawn inside add().
    for (int index = 0; index < 46; ++index)
    {
        const double x = kOrigin[0] + rng.Uniform(-180.0, 180.0);
        const double y = kOrigin[1] + rng.Uniform(-180.0, 180.0);
        const double speed = rng.Random() < 0.3 ? 0.0 : rng.Uniform(1.0, 1.7);
        const double radius = rng.Uniform(1.5, 6.0);
        add(EntityKind::CrowdNpc, {x, y, kOrigin[2]}, radius, speed, 0.0, std::nullopt, std::nullopt, 0);
    }
    const auto combatFlags = static_cast<uint8_t>(coopv2::kEntityCombat | coopv2::kEntityWeaponDrawn | coopv2::kEntityHostile);
    constexpr double kDeaths[] = {6.0, 9.0, 12.0};
    for (int index = 0; index < 10; ++index)
    {
        const double x = kOrigin[0] + 25.0 + rng.Uniform(-15.0, 15.0);
        const double y = kOrigin[1] + 30.0 + rng.Uniform(-15.0, 15.0);
        const std::optional<double> deathAt = index < 3 ? std::optional<double>(kDeaths[index]) : std::nullopt;
        const double radius = rng.Uniform(3.0, 5.0);
        add(EntityKind::CombatNpc, {x, y, kOrigin[2]}, radius, 2.5, 0.0,
            deathAt ? std::optional<double>(*deathAt + 4.0) : std::nullopt, deathAt, combatFlags);
    }
    for (int index = 0; index < 12; ++index)
    {
        const double x = kOrigin[0] + rng.Uniform(-60.0, 60.0);
        const double y = kOrigin[1] + rng.Uniform(-60.0, 60.0);
        const double radius = rng.Uniform(40.0, 130.0);
        const double speed = rng.Uniform(8.0, 14.0);
        add(EntityKind::Vehicle, {x, y, kOrigin[2]}, radius, speed, 0.0, std::nullopt, std::nullopt,
            static_cast<uint8_t>(coopv2::kEntityLights));
    }
    for (int wave = 0; wave < 8; ++wave)
    {
        for (int pair = 0; pair < 2; ++pair)
        {
            const double x = kOrigin[0] + rng.Uniform(-80.0, 80.0);
            const double y = kOrigin[1] + rng.Uniform(-80.0, 80.0);
            const double start = 3.0 + 3.0 * wave;
            const double radius = rng.Uniform(2.0, 5.0);
            const double speed = rng.Uniform(1.0, 1.6);
            add(EntityKind::CrowdNpc, {x, y, kOrigin[2]}, radius, speed, start, start + 6.0, std::nullopt, 0);
        }
    }
    return specs;
}

bool Alive(const EntitySpec& aSpec, double aT)
{
    return aT >= aSpec.spawnAt && (!aSpec.despawnAt || aT < *aSpec.despawnAt);
}

EntityTruth EntityTruthAt(const EntitySpec& aSpec, double aT)
{
    using coopv2::MoveState;
    const bool dead = aSpec.deathAt && aT >= *aSpec.deathAt;
    const double tMove = aSpec.deathAt ? std::min(aT, *aSpec.deathAt) : aT;
    const double omega = aSpec.speed / aSpec.radius;
    const double angle = aSpec.phase + omega * tMove;
    EntityTruth truth;
    truth.pos = {aSpec.center[0] + aSpec.radius * std::cos(angle), aSpec.center[1] + aSpec.radius * std::sin(angle),
                 aSpec.center[2]};
    if (aSpec.speed > 0.0 && !dead)
    {
        truth.vel = {-aSpec.radius * omega * std::sin(angle), aSpec.radius * omega * std::cos(angle), 0.0};
        truth.yaw = YawFromForward(truth.vel[0], truth.vel[1]);
    }
    else
    {
        truth.vel = {0.0, 0.0, 0.0};
        truth.yaw = PyFloatMod(aSpec.phase * kRadToDeg, 360.0);
    }
    truth.quat = YawQuat(truth.yaw);
    truth.flags = static_cast<uint8_t>(aSpec.flags | (dead ? coopv2::kEntityDead : 0));
    MoveState move = MoveState::Idle;
    if (dead)
        move = MoveState::Dead;
    else if (aSpec.kind == static_cast<uint8_t>(coopv2::EntityKind::Vehicle))
        move = MoveState::Vehicle;
    else if (aSpec.speed > 2.0)
        move = MoveState::Run;
    else if (aSpec.speed > 0.0)
        move = MoveState::Walk;
    truth.move = static_cast<uint8_t>(move);
    truth.health = dead ? 0 : 255;
    return truth;
}

// ---- players -------------------------------------------------------------------------------------

PlayerTruth PlayerTruthAt(uint8_t aRole, double aT, PlayerPath aPath)
{
    if (aPath == PlayerPath::Course)
    {
        return CourseTruth(aRole, aT);
    }
    PlayerTruth truth;
    if (aRole == static_cast<uint8_t>(coopv2::Role::Host))
    {
        const double omega = kHostSpeedMps / kHostRadiusM;
        const double angle = omega * aT;
        truth.pos = {kOrigin[0] + kHostRadiusM * std::cos(angle), kOrigin[1] + kHostRadiusM * std::sin(angle), kOrigin[2]};
        truth.vel = {-kHostRadiusM * omega * std::sin(angle), kHostRadiusM * omega * std::cos(angle), 0.0};
        truth.driving = false;
        truth.motion = "run";
        truth.move = static_cast<uint8_t>(coopv2::MoveState::Run);
    }
    else
    {
        const double omega = 2.0 * std::numbers::pi / kJoinerPeriodS;
        const double angle = omega * aT;
        const double a = kJoinerAxesM[0];
        const double b = kJoinerAxesM[1];
        const double cx = kOrigin[0] + kJoinerCenterOffset[0];
        const double cy = kOrigin[1] + kJoinerCenterOffset[1];
        truth.pos = {cx + a * std::cos(angle), cy + b * std::sin(angle), kOrigin[2] + 2.0 * std::sin(2 * angle)};
        truth.vel = {-a * omega * std::sin(angle), b * omega * std::cos(angle), 4.0 * omega * std::cos(2 * angle)};
        truth.driving = true;
        truth.motion = "drive";
        truth.move = static_cast<uint8_t>(coopv2::MoveState::Vehicle);
    }
    truth.yaw = YawFromForward(truth.vel[0], truth.vel[1]);
    truth.quat = YawQuat(truth.yaw);
    return truth;
}

double CoursePeriodS()
{
    return CourseFor(static_cast<uint8_t>(coopv2::Role::Host)).period;
}

const std::vector<std::string>& CourseMotions()
{
    static const std::vector<std::string> motions{"walk", "run", "sprint", "turn", "stop"};
    return motions;
}

double YawFromForward(double aForwardX, double aForwardY)
{
    return PyFloatMod(std::atan2(-aForwardX, aForwardY) * kRadToDeg, 360.0);
}
} // namespace coopnet::v2::demo
