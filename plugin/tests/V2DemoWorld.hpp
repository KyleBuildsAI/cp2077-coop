#pragma once

// The deterministic demo world of the relay repo's coopnet/testworld.py, for the C++ demo client
// (tests/V2DemoClient.cpp): the same entity table from the same seed (CPython's Mersenne Twister
// is reproduced exactly), the same entity and player ground truth, and the scripted on-foot
// course (walk, run, sprint, turns, stops). Both test clients evaluate the other player's truth
// at the render time, so the C++ and the Python client must agree here; tools/run_v2_demo.py
// compares a dump of this module with testworld.py before every run.

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace coopnet::v2::demo
{
using Vec3 = std::array<double, 3>;

inline constexpr Vec3 kOrigin{-1450.0, 180.0, 22.0};
inline constexpr uint64_t kEpochFact = 0xC0F7000000000001ull;
inline constexpr double kHostRadiusM = 10.0;
inline constexpr double kHostSpeedMps = 6.0;
inline constexpr double kJoinerAxesM[2] = {140.0, 90.0};
inline constexpr double kJoinerPeriodS = 50.0;
inline constexpr double kJoinerCenterOffset[2] = {40.0, 0.0};
inline constexpr uint64_t kRecordBase = 0x5EED000000000000ull;

// CPython's random.Random for an integer seed: MT19937 seeded with init_by_array over the seed's
// 32-bit words, random() from two outputs (53 bits), uniform(a, b) = a + (b - a) * random().
class PyRandom
{
public:
    explicit PyRandom(uint64_t aSeed);

    uint32_t NextU32();
    double Random();
    double Uniform(double aLow, double aHigh);

private:
    void InitGenrand(uint32_t aSeed);
    void InitByArray(const std::vector<uint32_t>& aKey);

    std::array<uint32_t, 624> m_state{};
    size_t m_index = 625;
};

struct EntitySpec
{
    uint16_t netId = 0;
    uint8_t kind = 0;
    Vec3 center{};
    double radius = 0.0;
    double speed = 0.0;
    double phase = 0.0;
    double spawnAt = 0.0;
    std::optional<double> despawnAt;
    std::optional<double> deathAt;
    uint8_t flags = 0;

    [[nodiscard]] uint64_t Record() const
    {
        return kRecordBase + kind;
    }
    [[nodiscard]] uint64_t Appearance() const
    {
        return netId;
    }
};

std::vector<EntitySpec> BuildEntities(uint64_t aSeed = 7);
bool Alive(const EntitySpec& aSpec, double aT);

struct EntityTruth
{
    Vec3 pos{};
    Vec3 vel{};
    double yaw = 0.0;
    std::array<double, 4> quat{0.0, 0.0, 0.0, 1.0};
    uint8_t move = 0;
    uint8_t flags = 0;
    uint8_t health = 255;
};

EntityTruth EntityTruthAt(const EntitySpec& aSpec, double aT);

enum class PlayerPath : uint8_t
{
    Demo,   // the host runs a 10 m circle, the joiner drives an ellipse
    Course, // both run the scripted course
};

struct PlayerTruth
{
    Vec3 pos{};
    Vec3 vel{};
    double yaw = 0.0;
    std::array<double, 4> quat{0.0, 0.0, 0.0, 1.0};
    bool driving = false;
    std::string motion; // "run", "drive" (demo); "walk", "run", "sprint", "turn", "stop" (course)
    uint8_t move = 2;   // MoveState (Run on the demo path)
};

// aT = relay time in seconds; aRole is the coopv2::Role value (1 host, anything else: joiner).
PlayerTruth PlayerTruthAt(uint8_t aRole, double aT, PlayerPath aPath);

// The course loop period in seconds (both roles use the same segments).
double CoursePeriodS();
// The kinds of movement the course reports errors for, in report order.
const std::vector<std::string>& CourseMotions();

// Cyberpunk yaw from a forward vector: 0 faces +Y, positive turns towards -X (degrees, [0, 360)).
double YawFromForward(double aForwardX, double aForwardY);
} // namespace coopnet::v2::demo
